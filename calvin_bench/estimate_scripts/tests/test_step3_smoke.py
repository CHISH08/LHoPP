from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np


def _tag_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class FakeCalvinEnv:
    def __init__(self) -> None:
        self._step_idx = 0

    def reset(self, robot_obs=None, scene_obs=None):
        del robot_obs, scene_obs
        self._step_idx = 0
        return self._obs()

    def get_info(self):
        return {"sim_step": self._step_idx}

    def step(self, action):
        del action
        self._step_idx += 1
        return self._obs(), 0.0, False, self.get_info()

    def close(self):
        return None

    def _obs(self):
        pixel = int(self._step_idx % 255)
        rgb = np.full((32, 32, 3), pixel, dtype=np.uint8)
        depth = np.full((32, 32), float(self._step_idx) / 100.0, dtype=np.float32)
        return {
            "rgb_obs": {
                "rgb_static": rgb.copy(),
                "rgb_gripper": rgb.copy(),
                "rgb_tactile": rgb.copy(),
            },
            "depth_obs": {
                "depth_static": depth.copy(),
                "depth_gripper": depth.copy(),
                "depth_tactile": depth.copy(),
            },
            "robot_obs": np.zeros(15, dtype=np.float32),
            "scene_obs": np.zeros(24, dtype=np.float32),
        }


class FakeTaskOracle:
    def get_task_info_for_set(self, start_info: Dict[str, Any], current_info: Dict[str, Any], target_set: set):
        # One env step is enough to solve current subtask in smoke mode.
        if int(current_info.get("sim_step", 0)) > int(start_info.get("sim_step", 0)):
            return set(target_set)
        return set()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test for CALVIN estimate_scripts step3 runtime.")
    parser.add_argument("--repo-root", default=".", help="Path to repository root.")
    parser.add_argument("--calvin-root", default="calvin_bench/calvin", help="Path to local CALVIN repo.")
    parser.add_argument("--dataset-path", default="calvin_bench/calvin/dataset/task_D_D", help="Path to local CALVIN dataset.")
    parser.add_argument("--official-total", type=int, default=40, help="Step1 official_total for test bundle.")
    parser.add_argument("--selected-total", type=int, default=6, help="Step1 selected_total for test bundle.")
    parser.add_argument("--episodes", type=int, default=10, help="How many episodes to execute in step3 smoke run.")
    parser.add_argument("--parallel-workers", type=int, default=2, help="Parallel workers for step3 smoke run.")
    parser.add_argument("--save-frames", action="store_true", help="Store step frames during smoke run.")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve()
    calvin_root = (repo_root / args.calvin_root).resolve()
    dataset_path = (repo_root / args.dataset_path).resolve()
    _assert(calvin_root.exists(), f"CALVIN root does not exist: {calvin_root}")
    _assert(dataset_path.exists(), f"Dataset path does not exist: {dataset_path}")

    import sys

    estimate_root = repo_root / "calvin_bench" / "estimate_scripts"
    sys.path.insert(0, str(estimate_root))
    from runtime import step3_runner as step3_module
    from utils.build_protocol_dataset import build_protocol_dataset
    from utils.build_scenario_contracts import build_scenario_contracts

    test_root = estimate_root / "test_runs" / f"test-step3-{_tag_now()}"
    bundle_root = test_root / "protocol_bundle"
    contracts_root = bundle_root / "contracts"
    run_root = test_root / "runs"

    build_protocol_dataset(
        calvin_root=calvin_root,
        output_root=bundle_root,
        seed=42,
        official_total=args.official_total,
        selected_total=args.selected_total,
        track="unified_ranking",
        split="validation",
        dry_run=False,
    )
    build_scenario_contracts(protocol_root=bundle_root, output_root=contracts_root, force=True)

    original_factory = step3_module._instantiate_env_and_oracle
    step3_module._instantiate_env_and_oracle = lambda calvin_root, dataset_path, show_gui: (FakeCalvinEnv(), FakeTaskOracle())
    try:
        summary = step3_module.run_calvin_benchmark_step3(
            calvin_root=calvin_root,
            dataset_path=dataset_path,
            protocol_root=bundle_root,
            contracts_root=contracts_root,
            run_root=run_root,
            model_id="smoke_python_model",
            model_family="test",
            model_backend="python",
            model_host="127.0.0.1",
            model_port=9000,
            model_timeout_sec=5.0,
            python_model_spec=str(estimate_root / "tests" / "mock_step3_model.py") + ":DeterministicStep3Model",
            python_model_kwargs={},
            parallel_workers=args.parallel_workers,
            benchmark_size=args.episodes,
            save_frames=bool(args.save_frames),
            max_episodes=args.episodes,
            allow_subtask_skip=True,
            allow_incompatible_conditions=True,
            show_gui=False,
        )
    finally:
        step3_module._instantiate_env_and_oracle = original_factory

    _assert(summary["status"] == "completed", "step3 summary status must be completed")
    _assert(int(summary["worker_errors_total"]) == 0, f"worker errors found: {summary['worker_errors_preview']}")
    _assert(int(summary["episodes_total"]) == args.episodes, "episodes_total mismatch")
    _assert(int(summary["steps_total"]) > 0, "steps_total must be > 0")

    run_dir = Path(summary["output_paths"]["run_dir"])
    _assert(run_dir.exists(), f"run_dir does not exist: {run_dir}")
    required_files = [
        run_dir / "run_summary.json",
        run_dir / "run_overview.json",
        run_dir / "logs" / "episodes_all.csv",
        run_dir / "logs" / "steps_all.csv",
        run_dir / "logs" / "events_all.csv",
        run_dir / "logs" / "episodes_index.csv",
        run_dir / "frames_manifest.csv",
    ]
    for path in required_files:
        _assert(path.exists(), f"Missing run artifact: {path}")

    episode_rows = _read_csv_rows(run_dir / "logs" / "episodes_all.csv")
    step_rows = _read_csv_rows(run_dir / "logs" / "steps_all.csv")
    event_rows = _read_csv_rows(run_dir / "logs" / "events_all.csv")
    _assert(len(episode_rows) == args.episodes, "episodes_all.csv row count mismatch")
    _assert(len(step_rows) > 0, "steps_all.csv must have rows")
    # events can be zero for ideal-only samples, but schema must exist.
    _assert(event_rows is not None, "events_all.csv must be readable")

    first_step = step_rows[0]
    for key in (
        "scenario_profile_id",
        "action_level_id",
        "observation_profile_id",
        "sensor_mask_before",
        "sensor_mask_after",
        "predict_time_ms",
        "executor_time_ms",
        "oracle_check_time_ms",
        "subsequence_success_len",
    ):
        _assert(key in first_step, f"Required step column missing: {key}")

    frames_manifest = _read_csv_rows(run_dir / "frames_manifest.csv")
    if args.save_frames:
        _assert(len(frames_manifest) > 0, "frames_manifest must be non-empty when --save-frames")
        missing_frames = [row["frame_path"] for row in frames_manifest if not Path(row["frame_path"]).exists()]
        _assert(len(missing_frames) == 0, f"Missing frame files: {missing_frames[:3]}")

    print(
        json.dumps(
            {
                "status": "pass",
                "test_root": str(test_root),
                "run_dir": str(run_dir),
                "episodes_total": int(summary["episodes_total"]),
                "steps_total": int(summary["steps_total"]),
                "events_total": int(summary["events_total"]),
                "frames_total": int(summary["frames_total"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

