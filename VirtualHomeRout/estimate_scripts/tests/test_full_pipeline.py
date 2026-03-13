import argparse
import csv
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class CheckResult:
    name: str
    ok: bool
    severity: str
    details: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tag_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _is_port_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _find_free_base_port(start: int, workers: int, search_span: int = 200) -> int:
    for base in range(start, start + search_span):
        if all(_is_port_free(base + idx) for idx in range(workers)):
            return base
    raise RuntimeError(f"Could not find free port range for workers={workers} starting at {start}")


def _find_free_port(start: int, search_span: int = 200) -> int:
    for port in range(start, start + search_span):
        if _is_port_free(port):
            return port
    raise RuntimeError(f"Could not find free port starting at {start}")


def _wait_port(host: str, port: int, timeout_sec: float = 15.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            sock.connect((host, port))
            return True
        except Exception:
            time.sleep(0.2)
        finally:
            sock.close()
    return False


def _extract_last_json(stdout: str) -> Optional[Dict[str, Any]]:
    lines = stdout.strip().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("{"):
            candidate = "\n".join(lines[i:])
            try:
                return json.loads(candidate)
            except Exception:
                continue
    return None


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_cmd(
    cmd: List[str],
    cwd: Path,
    log_path: Path,
    timeout_sec: int,
    env_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    started = time.time()
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env=env,
        )
        elapsed = time.time() - started
        log_text = []
        log_text.append(f"UTC: {_utc_now()}")
        log_text.append(f"CMD: {' '.join(cmd)}")
        log_text.append(f"EXIT_CODE: {proc.returncode}")
        log_text.append(f"DURATION_SEC: {elapsed:.3f}")
        log_text.append("\n--- STDOUT ---\n")
        log_text.append(proc.stdout or "")
        log_text.append("\n--- STDERR ---\n")
        log_text.append(proc.stderr or "")
        _write_text(log_path, "".join(log_text))
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "duration_sec": elapsed,
            "timeout": False,
        }
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - started
        out = exc.stdout or ""
        err = exc.stderr or ""
        log_text = []
        log_text.append(f"UTC: {_utc_now()}")
        log_text.append(f"CMD: {' '.join(cmd)}")
        log_text.append("EXIT_CODE: TIMEOUT\n")
        log_text.append(f"DURATION_SEC: {elapsed:.3f}\n")
        log_text.append("\n--- STDOUT ---\n")
        log_text.append(out)
        log_text.append("\n--- STDERR ---\n")
        log_text.append(err)
        _write_text(log_path, "".join(log_text))
        return {
            "returncode": 124,
            "stdout": out,
            "stderr": err,
            "duration_sec": elapsed,
            "timeout": True,
        }


def _is_known_exr_probe_failure(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return "openexr codec is disabled" in text or "opencv_io_enable_openexr" in text


def _latest_dir(root: Path, prefix: str) -> Path:
    dirs = [x for x in root.glob(f"{prefix}*") if x.is_dir()]
    if not dirs:
        raise RuntimeError(f"No directories with prefix={prefix} under {root}")
    return sorted(dirs, key=lambda x: x.stat().st_mtime, reverse=True)[0]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _write_report(
    report_dir: Path,
    checks: List[CheckResult],
    config: Dict[str, Any],
    step_summaries: Dict[str, Any],
    artifact_paths: Dict[str, str],
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    checks_json = [asdict(x) for x in checks]
    (report_dir / "checks.json").write_text(json.dumps(checks_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir / "step_summaries.json").write_text(
        json.dumps(step_summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (report_dir / "artifacts.json").write_text(json.dumps(artifact_paths, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [x for x in checks if not x.ok and x.severity == "error"]
    warnings = [x for x in checks if not x.ok and x.severity == "warning"]
    status = "PASS" if len(failed) == 0 else "FAIL"
    lines = []
    lines.append(f"# Full Pipeline Test Report\n\n")
    lines.append(f"- status: **{status}**\n")
    lines.append(f"- created_at_utc: `{_utc_now()}`\n")
    lines.append(f"- failed_checks: `{len(failed)}`\n")
    lines.append(f"- warning_checks: `{len(warnings)}`\n\n")
    lines.append("## Artifacts\n")
    for key, value in artifact_paths.items():
        lines.append(f"- {key}: `{value}`\n")
    lines.append("\n## Checks\n")
    for check in checks:
        icon = "OK" if check.ok else ("WARN" if check.severity == "warning" else "FAIL")
        lines.append(f"- [{icon}] `{check.name}`: {check.details}\n")
    (report_dir / "summary.md").write_text("".join(lines), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Full integration test for estimate_scripts steps 1,2,3,4 with artifacts."
    )
    parser.add_argument("--repo-root", default=".", help="Path to VirtualHomeRout repo root.")
    parser.add_argument("--unity-exe", default="dataset/windows_exec.v2.3.0/VirtualHome.exe")
    parser.add_argument(
        "--dataset-root",
        default="virtualhome/virtualhome/dataset/programs_processed_precond_nograb_morepreconds/executable_programs",
    )
    parser.add_argument("--per-stratum", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--track", default="unified_ranking")
    parser.add_argument("--parallel-workers", type=int, default=2)
    parser.add_argument("--step2-standby-seconds", type=int, default=4)
    parser.add_argument("--max-episodes", type=int, default=4)
    parser.add_argument("--model-timeout-sec", type=float, default=15.0)
    parser.add_argument("--save-frames", action="store_true")
    parser.add_argument("--allow-worker-errors", action="store_true")
    parser.add_argument("--command-timeout-sec", type=int, default=1800)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve()
    test_root = repo_root / "estimate_scripts" / "test_runs" / f"test-{_tag_now()}"
    logs_dir = test_root / "logs"
    report_dir = test_root / "report"
    protocol_root = test_root / "protocol_bundle"
    step2_runs_root = test_root / "runs_step2"
    step4_runs_root = test_root / "runs_step4"
    mock_root = test_root / "mock_model"
    for p in [logs_dir, report_dir, protocol_root, step2_runs_root, step4_runs_root, mock_root]:
        p.mkdir(parents=True, exist_ok=True)

    checks: List[CheckResult] = []
    step_summaries: Dict[str, Any] = {}
    artifacts: Dict[str, str] = {
        "test_root": str(test_root),
        "logs_dir": str(logs_dir),
        "report_dir": str(report_dir),
        "protocol_root": str(protocol_root),
        "step2_runs_root": str(step2_runs_root),
        "step4_runs_root": str(step4_runs_root),
        "mock_root": str(mock_root),
    }

    def add_check(name: str, ok: bool, details: str, severity: str = "error") -> None:
        checks.append(CheckResult(name=name, ok=ok, severity=severity, details=details))

    base_port = _find_free_base_port(8090, args.parallel_workers)
    model_port = _find_free_port(19000)
    artifacts["selected_base_port"] = str(base_port)
    artifacts["selected_model_port"] = str(model_port)

    mock_log = logs_dir / "mock_model_server.log"
    requests_log = mock_root / "requests.jsonl"
    stats_json = mock_root / "server_stats.json"
    mock_proc: Optional[subprocess.Popen] = None

    config = {
        "repo_root": str(repo_root),
        "unity_exe": str((repo_root / args.unity_exe).resolve()),
        "dataset_root": str((repo_root / args.dataset_root).resolve()),
        "per_stratum": args.per_stratum,
        "seed": args.seed,
        "track": args.track,
        "parallel_workers": args.parallel_workers,
        "step2_standby_seconds": args.step2_standby_seconds,
        "max_episodes": args.max_episodes,
        "model_timeout_sec": args.model_timeout_sec,
        "save_frames": bool(args.save_frames),
        "allow_worker_errors": bool(args.allow_worker_errors),
        "base_port": base_port,
        "model_port": model_port,
    }

    try:
        unity_exe = (repo_root / args.unity_exe).resolve()
        add_check("unity_exe_exists", unity_exe.exists(), f"unity_exe={unity_exe}")
        if not unity_exe.exists():
            raise RuntimeError(f"Unity executable does not exist: {unity_exe}")
        cmd_env = {"OPENCV_IO_ENABLE_OPENEXR": "1"}

        # STEP 1
        step1_cmd = [
            sys.executable,
            "estimate_scripts/main.py",
            "--step",
            "1",
            "--dataset-root",
            str((repo_root / args.dataset_root).resolve()),
            "--output-root",
            str(protocol_root),
            "--seed",
            str(args.seed),
            "--per-stratum",
            str(args.per_stratum),
            "--track",
            str(args.track),
        ]
        step1_res = _run_cmd(
            step1_cmd,
            cwd=repo_root,
            log_path=logs_dir / "step1.log",
            timeout_sec=args.command_timeout_sec,
            env_overrides=cmd_env,
        )
        step_summaries["step1_cmd"] = step1_res
        step1_json = _extract_last_json(step1_res["stdout"])
        add_check("step1_exit_code", step1_res["returncode"] == 0, f"returncode={step1_res['returncode']}")
        add_check("step1_json_summary", step1_json is not None, "json summary parsed")
        if step1_res["returncode"] != 0:
            raise RuntimeError("Step 1 failed")

        selected_csv = protocol_root / "data" / "selected_tasks.csv"
        task_manifest = protocol_root / "manifest" / "task_manifest.json"
        benchmark_manifest = protocol_root / "manifest" / "benchmark_manifest.json"
        add_check("step1_selected_csv_exists", selected_csv.exists(), str(selected_csv))
        add_check("step1_task_manifest_exists", task_manifest.exists(), str(task_manifest))
        add_check("step1_benchmark_manifest_exists", benchmark_manifest.exists(), str(benchmark_manifest))
        if not (selected_csv.exists() and task_manifest.exists() and benchmark_manifest.exists()):
            raise RuntimeError("Step 1 artifacts are incomplete")

        selected_rows = _read_csv_rows(selected_csv)
        expected_selected = args.per_stratum * 3
        add_check(
            "step1_selected_rows_count",
            len(selected_rows) == expected_selected,
            f"selected_rows={len(selected_rows)} expected={expected_selected}",
        )

        # STEP 3
        step3_cmd = [
            sys.executable,
            "estimate_scripts/main.py",
            "--step",
            "3",
            "--protocol-root",
            str(protocol_root),
            "--contracts-output-root",
            str(protocol_root / "contracts"),
            "--contracts-force",
        ]
        step3_res = _run_cmd(
            step3_cmd,
            cwd=repo_root,
            log_path=logs_dir / "step3.log",
            timeout_sec=args.command_timeout_sec,
            env_overrides=cmd_env,
        )
        step_summaries["step3_cmd"] = step3_res
        step3_json = _extract_last_json(step3_res["stdout"])
        add_check("step3_exit_code", step3_res["returncode"] == 0, f"returncode={step3_res['returncode']}")
        add_check("step3_json_summary", step3_json is not None, "json summary parsed")
        if step3_res["returncode"] != 0:
            raise RuntimeError("Step 3 failed")

        contracts_root = protocol_root / "contracts"
        required_contracts = [
            contracts_root / "episodes_contracts.csv",
            contracts_root / "steps_contracts.csv",
            contracts_root / "events_schedule.csv",
            contracts_root / "conditions_contracts.json",
            contracts_root / "schema_refs.json",
            contracts_root / "scenario_contract_manifest.json",
        ]
        for path in required_contracts:
            add_check(f"step3_artifact_{path.name}", path.exists(), str(path))
        if not all(x.exists() for x in required_contracts):
            raise RuntimeError("Step 3 artifacts are incomplete")

        # STEP 2
        step2_cmd = [
            sys.executable,
            "estimate_scripts/main.py",
            "--step",
            "2",
            "--unity-exe",
            str(unity_exe),
            "--parallel-workers",
            str(args.parallel_workers),
            "--base-port",
            str(base_port),
            "--scene-id",
            "0",
            "--run-root",
            str(step2_runs_root),
            "--standby-seconds",
            str(args.step2_standby_seconds),
        ]
        step2_res = _run_cmd(
            step2_cmd,
            cwd=repo_root,
            log_path=logs_dir / "step2.log",
            timeout_sec=args.command_timeout_sec,
            env_overrides=cmd_env,
        )
        step_summaries["step2_cmd"] = step2_res
        step2_json = _extract_last_json(step2_res["stdout"])
        step2_known_exr = _is_known_exr_probe_failure(step2_res["stdout"], step2_res["stderr"])
        if step2_res["returncode"] == 0:
            add_check("step2_exit_code", True, f"returncode={step2_res['returncode']}")
            add_check("step2_json_summary", step2_json is not None, "json summary parsed")
        elif step2_known_exr:
            add_check(
                "step2_known_openexr_issue",
                True,
                "Step2 failed on depth/EXR probe; continuing test as known environment limitation.",
                severity="warning",
            )
            add_check("step2_exit_code", False, f"returncode={step2_res['returncode']}", severity="warning")
        else:
            add_check("step2_exit_code", False, f"returncode={step2_res['returncode']}")
            add_check("step2_json_summary", step2_json is not None, "json summary parsed")
            raise RuntimeError("Step 2 failed")

        step2_run_dir = _latest_dir(step2_runs_root, "unity_bootstrap_")
        artifacts["step2_run_dir"] = str(step2_run_dir)
        env_registry = step2_run_dir / "env_setup" / "env_registry.csv"
        sensor_probe = step2_run_dir / "env_setup" / "sensor_probe.csv"
        interaction_probe = step2_run_dir / "env_setup" / "interaction_probe.csv"
        health_report = step2_run_dir / "env_setup" / "health_report.json"
        for p in [env_registry, sensor_probe, interaction_probe, health_report]:
            add_check(f"step2_file_{p.name}", p.exists(), str(p))
        if not all(x.exists() for x in [env_registry, sensor_probe, interaction_probe, health_report]):
            raise RuntimeError("Step 2 logs are incomplete")

        env_rows = _read_csv_rows(env_registry)
        ready_rows = [x for x in env_rows if x.get("status") == "ready"]
        add_check("step2_env_registry_count", len(env_rows) == args.parallel_workers, f"rows={len(env_rows)} workers={args.parallel_workers}")
        if step2_res["returncode"] == 0:
            add_check("step2_all_ready", len(ready_rows) == args.parallel_workers, f"ready={len(ready_rows)} workers={args.parallel_workers}")
        else:
            add_check(
                "step2_all_ready",
                len(ready_rows) == args.parallel_workers,
                f"ready={len(ready_rows)} workers={args.parallel_workers}",
                severity="warning",
            )
        health = json.loads(health_report.read_text(encoding="utf-8"))
        if step2_res["returncode"] == 0:
            add_check("step2_health_ready", health.get("overall_status") == "ready", f"overall_status={health.get('overall_status')}")
        else:
            add_check(
                "step2_health_ready",
                health.get("overall_status") == "ready",
                f"overall_status={health.get('overall_status')}",
                severity="warning",
            )

        # Start mock model server for step 4
        mock_cmd = [
            sys.executable,
            "estimate_scripts/runtime/mock_async_model_server.py",
            "--host",
            "127.0.0.1",
            "--port",
            str(model_port),
            "--min-delay-ms",
            "40",
            "--max-delay-ms",
            "140",
            "--requests-log-path",
            str(requests_log),
            "--stats-path",
            str(stats_json),
        ]
        with mock_log.open("w", encoding="utf-8") as mock_out:
            mock_proc = subprocess.Popen(mock_cmd, cwd=str(repo_root), stdout=mock_out, stderr=subprocess.STDOUT, text=True)
        add_check("mock_server_started", mock_proc is not None and mock_proc.poll() is None, f"pid={getattr(mock_proc, 'pid', None)}")
        if not _wait_port("127.0.0.1", model_port, timeout_sec=20.0):
            add_check("mock_server_port_ready", False, f"port={model_port} did not open")
            raise RuntimeError("Mock server did not open port")
        add_check("mock_server_port_ready", True, f"port={model_port} is open")

        # STEP 4
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
            str(base_port),
            "--protocol-root",
            str(protocol_root),
            "--contracts-root",
            str(protocol_root / "contracts"),
            "--tasks-root",
            str(protocol_root / "data" / "tasks"),
            "--run-root",
            str(step4_runs_root),
            "--model-id",
            "test_async_random_model",
            "--model-family",
            "mock",
            "--model-host",
            "127.0.0.1",
            "--model-port",
            str(model_port),
            "--model-timeout-sec",
            str(args.model_timeout_sec),
            "--max-episodes",
            str(args.max_episodes),
            "--frame-mode",
            "normal",
            "--frame-camera-index",
            "0",
        ]
        if args.save_frames:
            step4_cmd.append("--save-frames")
        step4_res = _run_cmd(
            step4_cmd,
            cwd=repo_root,
            log_path=logs_dir / "step4.log",
            timeout_sec=args.command_timeout_sec,
            env_overrides=cmd_env,
        )
        step_summaries["step4_cmd"] = step4_res
        step4_json = _extract_last_json(step4_res["stdout"])
        add_check("step4_exit_code", step4_res["returncode"] == 0, f"returncode={step4_res['returncode']}")
        add_check("step4_json_summary", step4_json is not None, "json summary parsed")
        if step4_res["returncode"] != 0:
            raise RuntimeError("Step 4 failed")

        step4_run_dir = _latest_dir(step4_runs_root, "vh_step4_")
        artifacts["step4_run_dir"] = str(step4_run_dir)
        run_summary_path = step4_run_dir / "run_summary.json"
        add_check("step4_run_summary_exists", run_summary_path.exists(), str(run_summary_path))
        if not run_summary_path.exists():
            raise RuntimeError("Step 4 run_summary missing")

        run_summary = json.loads(run_summary_path.read_text(encoding="utf-8"))
        add_check("step4_status_completed", run_summary.get("status") == "completed", f"status={run_summary.get('status')}")
        add_check("step4_episodes_total", _safe_int(run_summary.get("episodes_total")) == args.max_episodes, f"episodes_total={run_summary.get('episodes_total')} expected={args.max_episodes}")
        add_check("step4_steps_positive", _safe_int(run_summary.get("steps_total")) > 0, f"steps_total={run_summary.get('steps_total')}")
        add_check(
            "step4_selection_fields",
            all(
                key in run_summary
                for key in [
                    "episodes_planned_total",
                    "episodes_selected_total",
                    "episodes_truncated_by_max_episodes",
                    "strata_planned_counts",
                    "strata_selected_counts",
                ]
            ),
            "planned/selected/truncated and stratum counters present",
        )
        add_check(
            "step4_selected_equals_total",
            _safe_int(run_summary.get("episodes_selected_total")) == _safe_int(run_summary.get("episodes_total")),
            f"selected={run_summary.get('episodes_selected_total')} total={run_summary.get('episodes_total')}",
        )
        if _safe_int(run_summary.get("episodes_truncated_by_max_episodes")) > 0:
            add_check(
                "step4_truncated_notice",
                False,
                f"episodes_truncated_by_max_episodes={run_summary.get('episodes_truncated_by_max_episodes')}",
                severity="warning",
            )

        worker_errors_total = _safe_int(run_summary.get("worker_errors_total"), 0)
        worker_errors_preview = [str(x) for x in (run_summary.get("worker_errors_preview", []) or [])]
        known_transient = (
            worker_errors_total > 0
            and all(
                ("read timed out" in msg.lower()) or ("observation_failed" in msg.lower())
                for msg in worker_errors_preview
            )
        )
        if worker_errors_total == 0:
            add_check("step4_worker_errors", True, "worker_errors_total=0")
        elif args.allow_worker_errors or known_transient:
            add_check(
                "step4_worker_errors",
                False,
                f"worker_errors_total={worker_errors_total} preview={worker_errors_preview[:3]}",
                severity="warning",
            )
        else:
            add_check(
                "step4_worker_errors",
                False,
                f"worker_errors_total={worker_errors_total} preview={worker_errors_preview[:3]}",
            )

        cells_dir = step4_run_dir / "cells"
        add_check("step4_cells_dir_exists", cells_dir.exists(), str(cells_dir))
        if cells_dir.exists():
            cell_dirs = [x for x in cells_dir.iterdir() if x.is_dir()]
            add_check("step4_cells_non_empty", len(cell_dirs) > 0, f"cell_dirs={len(cell_dirs)}")
            for cell in cell_dirs:
                ep_csv = cell / "episodes.csv"
                st_csv = cell / "steps.csv"
                ev_csv = cell / "events.csv"
                md_json = cell / "metadata.json"
                add_check(f"step4_cell_file_{cell.name}_episodes", ep_csv.exists(), str(ep_csv))
                add_check(f"step4_cell_file_{cell.name}_steps", st_csv.exists(), str(st_csv))
                add_check(f"step4_cell_file_{cell.name}_events", ev_csv.exists(), str(ev_csv))
                add_check(f"step4_cell_file_{cell.name}_metadata", md_json.exists(), str(md_json))
                if ep_csv.exists():
                    add_check(f"step4_cell_rows_{cell.name}_episodes", len(_read_csv_rows(ep_csv)) > 0, f"rows={len(_read_csv_rows(ep_csv))}")
                if st_csv.exists():
                    st_rows = _read_csv_rows(st_csv)
                    add_check(f"step4_cell_rows_{cell.name}_steps", len(st_rows) > 0, f"rows={len(st_rows)}")
                    if st_rows:
                        first = st_rows[0]
                        non_empty_timing = all(
                            str(first.get(k, "")).strip() != ""
                            for k in ["decision_time_step_sec", "sim_exec_time_step_sec", "episode_wallclock_step_sec"]
                        )
                        add_check(f"step4_timing_fields_{cell.name}", non_empty_timing, "decision/sim/wallclock fields present")

        run_overview_path = step4_run_dir / "run_overview.json"
        add_check("step4_run_overview_exists", run_overview_path.exists(), str(run_overview_path))
        flat_logs_dir = step4_run_dir / "logs"
        episodes_all_csv = flat_logs_dir / "episodes_all.csv"
        steps_all_csv = flat_logs_dir / "steps_all.csv"
        events_all_csv = flat_logs_dir / "events_all.csv"
        episodes_index_csv = flat_logs_dir / "episodes_index.csv"
        add_check("step4_flat_logs_dir_exists", flat_logs_dir.exists(), str(flat_logs_dir))
        add_check("step4_flat_episodes_all_exists", episodes_all_csv.exists(), str(episodes_all_csv))
        add_check("step4_flat_steps_all_exists", steps_all_csv.exists(), str(steps_all_csv))
        add_check("step4_flat_events_all_exists", events_all_csv.exists(), str(events_all_csv))
        add_check("step4_episodes_index_exists", episodes_index_csv.exists(), str(episodes_index_csv))
        if episodes_index_csv.exists():
            index_rows = _read_csv_rows(episodes_index_csv)
            add_check("step4_episodes_index_non_empty", len(index_rows) > 0, f"rows={len(index_rows)}")

        frames_manifest = step4_run_dir / "frames_manifest.csv"
        add_check("step4_frames_manifest_exists", frames_manifest.exists(), str(frames_manifest))
        if args.save_frames and frames_manifest.exists():
            frame_rows = _read_csv_rows(frames_manifest)
            add_check("step4_frames_rows_positive", len(frame_rows) > 0, f"rows={len(frame_rows)}")
            missing_frames = [row["frame_path"] for row in frame_rows if not Path(row["frame_path"]).exists()]
            add_check("step4_frame_files_exist", len(missing_frames) == 0, f"missing_frames={len(missing_frames)}")

        # strict-blind & async checks from mock logs
        add_check("mock_requests_log_exists", requests_log.exists(), str(requests_log))
        add_check("mock_stats_exists", stats_json.exists(), str(stats_json))
        if requests_log.exists():
            req_rows = []
            for line in requests_log.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    req_rows.append(json.loads(line))
            add_check("mock_requests_non_empty", len(req_rows) > 0, f"rows={len(req_rows)}")
            strict_violations = [x for x in req_rows if bool(x.get("has_forbidden_keys"))]
            add_check("strict_blind_no_forbidden_keys", len(strict_violations) == 0, f"violations={len(strict_violations)}")
            slots = sorted(
                set(int(x["worker_slot"]) for x in req_rows if str(x.get("worker_slot", "")).isdigit())
            )
            expected_active_slots = min(args.parallel_workers, args.max_episodes)
            add_check("mock_worker_slots_coverage", len(slots) >= expected_active_slots, f"active_slots={len(slots)} expected_min={expected_active_slots}")
        if stats_json.exists():
            stats = json.loads(stats_json.read_text(encoding="utf-8"))
            max_inflight = _safe_int(stats.get("max_inflight"), 0)
            if args.parallel_workers > 1 and args.max_episodes > 1:
                add_check("mock_async_parallel_observed", max_inflight >= 2, f"max_inflight={max_inflight}")
            else:
                add_check("mock_async_parallel_observed", True, f"max_inflight={max_inflight}", severity="warning")

    except Exception as exc:
        add_check("pipeline_exception", False, f"{type(exc).__name__}: {exc}")
        _write_text(logs_dir / "exception_traceback.log", traceback.format_exc())
    finally:
        if mock_proc is not None:
            try:
                mock_proc.terminate()
                mock_proc.wait(timeout=5)
            except Exception:
                try:
                    mock_proc.kill()
                except Exception:
                    pass

        _write_report(
            report_dir=report_dir,
            checks=checks,
            config=config,
            step_summaries=step_summaries,
            artifact_paths=artifacts,
        )

    hard_failures = [x for x in checks if (not x.ok) and x.severity == "error"]
    print(f"[full-test] artifacts={test_root}")
    print(f"[full-test] checks_total={len(checks)} failures={len(hard_failures)}")
    if hard_failures:
        for fail in hard_failures:
            print(f"[full-test][FAIL] {fail.name}: {fail.details}")
        raise SystemExit(1)
    print("[full-test] PASS")


if __name__ == "__main__":
    main()
