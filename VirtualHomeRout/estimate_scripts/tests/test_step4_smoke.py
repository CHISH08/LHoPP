import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


def _wait_port(host: str, port: int, timeout_sec: float = 10.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            time.sleep(0.2)
        finally:
            try:
                s.close()
            except Exception:
                pass
    return False


def _latest_step4_run(run_root: Path) -> Path:
    candidates = [x for x in run_root.glob("vh_step4_*") if x.is_dir()]
    if not candidates:
        raise RuntimeError(f"No step4 run folders found under {run_root}")
    return sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True)[0]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _count_csv_rows(path: Path) -> int:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if len(lines) <= 1:
        return 0
    return len(lines) - 1


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test for step4 runtime with async mock model.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Path to VirtualHomeRout repository root.",
    )
    parser.add_argument(
        "--unity-exe",
        default="dataset/windows_exec.v2.3.0/VirtualHome.exe",
        help="Path to Unity executable (relative to repo root).",
    )
    parser.add_argument("--parallel-workers", type=int, default=2)
    parser.add_argument("--base-port", type=int, default=8090)
    parser.add_argument("--model-port", type=int, default=19000)
    parser.add_argument("--model-timeout-sec", type=float, default=8.0)
    parser.add_argument("--max-episodes", type=int, default=8)
    parser.add_argument("--save-frames", action="store_true")
    parser.add_argument("--run-root", default="estimate_scripts/runs")
    parser.add_argument("--protocol-root", default="estimate_scripts/protocol_bundle")
    parser.add_argument("--contracts-root", default="estimate_scripts/protocol_bundle/contracts")
    parser.add_argument("--tasks-root", default="estimate_scripts/protocol_bundle/data/tasks")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve()
    run_root = (repo_root / args.run_root).resolve()
    unity_exe = (repo_root / args.unity_exe).resolve()

    if not unity_exe.exists():
        raise FileNotFoundError(f"Unity executable not found: {unity_exe}")

    mock_dir = run_root / "mock_model_smoke"
    mock_dir.mkdir(parents=True, exist_ok=True)
    requests_log_path = mock_dir / "requests.jsonl"
    stats_path = mock_dir / "server_stats.json"
    if requests_log_path.exists():
        requests_log_path.unlink()
    if stats_path.exists():
        stats_path.unlink()

    server_cmd = [
        sys.executable,
        str(repo_root / "estimate_scripts" / "runtime" / "mock_async_model_server.py"),
        "--host",
        "127.0.0.1",
        "--port",
        str(args.model_port),
        "--min-delay-ms",
        "60",
        "--max-delay-ms",
        "150",
        "--requests-log-path",
        str(requests_log_path),
        "--stats-path",
        str(stats_path),
    ]
    server_proc = subprocess.Popen(server_cmd, cwd=str(repo_root))
    try:
        if not _wait_port("127.0.0.1", args.model_port, timeout_sec=15.0):
            raise RuntimeError("Mock model server did not open the port in time")

        step4_cmd = [
            sys.executable,
            "estimate_scripts/main.py",
            "--step",
            "4",
            "--unity-exe",
            str(unity_exe),
            "--parallel-workers",
            str(args.parallel_workers),
            "--base-port",
            str(args.base_port),
            "--protocol-root",
            str((repo_root / args.protocol_root).resolve()),
            "--contracts-root",
            str((repo_root / args.contracts_root).resolve()),
            "--tasks-root",
            str((repo_root / args.tasks_root).resolve()),
            "--run-root",
            str(run_root),
            "--model-id",
            "mock_async_random",
            "--model-family",
            "mock",
            "--model-host",
            "127.0.0.1",
            "--model-port",
            str(args.model_port),
            "--model-timeout-sec",
            str(args.model_timeout_sec),
            "--max-episodes",
            str(args.max_episodes),
        ]
        if args.save_frames:
            step4_cmd.append("--save-frames")

        started = time.time()
        res = subprocess.run(step4_cmd, cwd=str(repo_root), check=False)
        duration = time.time() - started
        _assert(res.returncode == 0, f"Step4 exited with code={res.returncode}")
        print(f"[smoke] step4 finished in {duration:.2f}s")
    finally:
        try:
            server_proc.terminate()
            server_proc.wait(timeout=5)
        except Exception:
            try:
                server_proc.kill()
            except Exception:
                pass

    run_dir = _latest_step4_run(run_root)
    print(f"[smoke] run_dir={run_dir}")

    summary = _read_json(run_dir / "run_summary.json")
    _assert(summary.get("status") == "completed", "run_summary.status != completed")
    _assert(int(summary.get("episodes_total", 0)) == args.max_episodes, "episodes_total mismatch")
    _assert(int(summary.get("steps_total", 0)) > 0, "steps_total must be > 0")
    for key in (
        "episodes_planned_total",
        "episodes_selected_total",
        "episodes_truncated_by_max_episodes",
        "strata_planned_counts",
        "strata_selected_counts",
    ):
        _assert(key in summary, f"run_summary missing selection key: {key}")

    cells_dir = run_dir / "cells"
    _assert(cells_dir.exists(), "cells_dir missing")
    cell_dirs = [x for x in cells_dir.iterdir() if x.is_dir()]
    _assert(len(cell_dirs) > 0, "no condition cells found")
    for cell in cell_dirs:
        for name in ("episodes.csv", "steps.csv", "events.csv", "metadata.json"):
            _assert((cell / name).exists(), f"missing {cell / name}")
        _assert(_count_csv_rows(cell / "episodes.csv") > 0, f"{cell / 'episodes.csv'} has no rows")
        _assert(_count_csv_rows(cell / "steps.csv") > 0, f"{cell / 'steps.csv'} has no rows")

    frames_manifest = run_dir / "frames_manifest.csv"
    _assert(frames_manifest.exists(), "frames_manifest.csv missing")
    flat_logs_dir = run_dir / "logs"
    _assert(flat_logs_dir.exists(), "flat logs dir missing")
    for name in ("episodes_all.csv", "steps_all.csv", "events_all.csv", "episodes_index.csv"):
        _assert((flat_logs_dir / name).exists(), f"missing {flat_logs_dir / name}")
    if args.save_frames:
        _assert(_count_csv_rows(frames_manifest) > 0, "frames_manifest has no rows while --save-frames set")

    _assert(requests_log_path.exists(), "mock requests log missing")
    request_logs = _read_jsonl(requests_log_path)
    _assert(len(request_logs) > 0, "mock requests log is empty")
    forbidden_hits = [x for x in request_logs if bool(x.get("has_forbidden_keys"))]
    _assert(len(forbidden_hits) == 0, "strict-blind violated: forbidden keys found in model payload")

    slots = sorted(set(int(x.get("worker_slot", -1)) for x in request_logs if str(x.get("worker_slot", "")).isdigit()))
    _assert(len(slots) >= 1, "No worker_slot values found in model requests")
    if args.parallel_workers > 1 and args.max_episodes >= args.parallel_workers:
        _assert(len(slots) == args.parallel_workers, "Not all workers sent requests to model")

    _assert(stats_path.exists(), "mock server stats missing")
    stats = _read_json(stats_path)
    _assert(int(stats.get("requests_total", 0)) >= len(request_logs), "stats.requests_total inconsistent")
    if args.parallel_workers > 1:
        _assert(int(stats.get("max_inflight", 0)) >= 2, "max_inflight < 2, async parallel processing not observed")

    print("[smoke] PASS")


if __name__ == "__main__":
    main()
