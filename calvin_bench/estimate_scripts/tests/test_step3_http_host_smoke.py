from __future__ import annotations

import argparse
import csv
import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import URLError
from urllib.request import urlopen

import numpy as np


def _tag_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_server_ready(host: str, port: int, timeout_sec: float) -> None:
    deadline = time.time() + timeout_sec
    url = f"http://{host}:{port}/health"
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=0.5) as response:
                body = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and body.get("status") == "ok":
                    return
        except (URLError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"mock random model server did not become ready: {url}")


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
        if int(current_info.get("sim_step", 0)) > int(start_info.get("sim_step", 0)):
            return set(target_set)
        return set()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test for step3 with host HTTP random model server.")
    parser.add_argument("--repo-root", default=".", help="Path to repository root.")
    parser.add_argument("--calvin-root", default="calvin_bench/calvin", help="Path to local CALVIN repo.")
    parser.add_argument("--dataset-path", default="calvin_bench/calvin/dataset/task_D_D", help="Path to local CALVIN dataset.")
    parser.add_argument("--official-total", type=int, default=40, help="Step1 official_total for test bundle.")
    parser.add_argument("--selected-total", type=int, default=6, help="Step1 selected_total for test bundle.")
    parser.add_argument("--episodes", type=int, default=10, help="How many episodes to execute in step3 smoke run.")
    parser.add_argument("--parallel-workers", type=int, default=2, help="Parallel workers for step3 smoke run.")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve()
    calvin_root = (repo_root / args.calvin_root).resolve()
    dataset_path = (repo_root / args.dataset_path).resolve()
    _assert(calvin_root.exists(), f"CALVIN root does not exist: {calvin_root}")
    _assert(dataset_path.exists(), f"Dataset path does not exist: {dataset_path}")

    estimate_root = repo_root / "calvin_bench" / "estimate_scripts"
    sys.path.insert(0, str(estimate_root))
    from runtime import step3_runner as step3_module
    from utils.build_protocol_dataset import build_protocol_dataset
    from utils.build_scenario_contracts import build_scenario_contracts

    test_root = estimate_root / "test_runs" / f"test-step3-http-{_tag_now()}"
    bundle_root = test_root / "protocol_bundle"
    contracts_root = bundle_root / "contracts"
    run_root = test_root / "runs"
    server_dir = test_root / "http_server"
    server_script = estimate_root / "runtime" / "mock_random_model_server.py"
    requests_log_path = server_dir / "requests.jsonl"
    stats_path = server_dir / "server_stats.json"

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

    port = _pick_free_port()
    server_cmd = [
        sys.executable,
        str(server_script),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--seed",
        "123",
        "--requests-log-path",
        str(requests_log_path),
        "--stats-path",
        str(stats_path),
        "--min-delay-ms",
        "1",
        "--max-delay-ms",
        "3",
    ]

    server_proc = subprocess.Popen(
        server_cmd,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    original_factory = step3_module._instantiate_env_and_oracle
    try:
        _wait_server_ready(host="127.0.0.1", port=port, timeout_sec=10.0)

        step3_module._instantiate_env_and_oracle = (
            lambda calvin_root, dataset_path, show_gui: (FakeCalvinEnv(), FakeTaskOracle())
        )
        summary = step3_module.run_calvin_benchmark_step3(
            calvin_root=calvin_root,
            dataset_path=dataset_path,
            protocol_root=bundle_root,
            contracts_root=contracts_root,
            run_root=run_root,
            model_id="smoke_http_model",
            model_family="test",
            model_backend="http",
            model_host="127.0.0.1",
            model_port=port,
            model_timeout_sec=5.0,
            python_model_spec=None,
            python_model_kwargs={},
            parallel_workers=args.parallel_workers,
            benchmark_size=args.episodes,
            save_frames=False,
            max_episodes=args.episodes,
            allow_subtask_skip=True,
            allow_incompatible_conditions=True,
            show_gui=False,
        )
    finally:
        step3_module._instantiate_env_and_oracle = original_factory
        try:
            server_proc.terminate()
            server_proc.wait(timeout=5)
        except Exception:
            server_proc.kill()
            server_proc.wait(timeout=5)

    _assert(summary["status"] == "completed", f"step3 summary status must be completed: {summary['status']}")
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
        requests_log_path,
        stats_path,
    ]
    for path in required_files:
        _assert(path.exists(), f"Missing expected artifact: {path}")

    step_rows = _read_csv_rows(run_dir / "logs" / "steps_all.csv")
    _assert(len(step_rows) > 0, "steps_all.csv must have rows")
    for key in (
        "model_status",
        "scenario_profile_id",
        "action_level_id",
        "predict_time_ms",
        "executor_time_ms",
        "oracle_check_time_ms",
    ):
        _assert(key in step_rows[0], f"Required step column missing: {key}")
    _assert(any(row.get("model_status") == "ok" for row in step_rows), "at least one step must have model_status=ok")

    request_rows = _read_jsonl(requests_log_path)
    _assert(len(request_rows) == int(summary["steps_total"]), "request log count must match steps_total")
    _assert(any(row.get("status") == "ok" for row in request_rows), "at least one HTTP request must be ok")

    stats_obj = json.loads(stats_path.read_text(encoding="utf-8"))
    _assert(int(stats_obj.get("requests_total", 0)) == len(request_rows), "stats requests_total mismatch")

    server_preview = ""
    if server_proc.stdout is not None:
        try:
            server_preview = server_proc.stdout.read()[:400]
        except Exception:
            server_preview = ""

    print(
        json.dumps(
            {
                "status": "pass",
                "test_root": str(test_root),
                "run_dir": str(run_dir),
                "episodes_total": int(summary["episodes_total"]),
                "steps_total": int(summary["steps_total"]),
                "http_requests_total": len(request_rows),
                "server_output_preview": server_preview,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
