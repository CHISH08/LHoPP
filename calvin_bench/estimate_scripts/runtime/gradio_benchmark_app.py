import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, List, Optional, Tuple

import cv2
import gradio as gr


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALVIN_ROOT = Path(os.getenv("CALVIN_ROOT", str(REPO_ROOT / "calvin"))).resolve()
DEFAULT_DATASET_PATH = Path(
    os.getenv("CALVIN_DATASET_PATH", str(DEFAULT_CALVIN_ROOT / "dataset" / "task_D_D"))
).resolve()
DEFAULT_PROTOCOL_ROOT = Path(
    os.getenv("CALVIN_PROTOCOL_ROOT", str(REPO_ROOT / "estimate_scripts" / "protocol_bundle"))
).resolve()
DEFAULT_CONTRACTS_ROOT = Path(
    os.getenv("CALVIN_CONTRACTS_ROOT", str(DEFAULT_PROTOCOL_ROOT / "contracts"))
).resolve()
DEFAULT_RUN_ROOT = Path(os.getenv("CALVIN_RUN_ROOT", str(REPO_ROOT / "estimate_scripts" / "runs"))).resolve()
DEFAULT_TEST_RUN_ROOT = Path(
    os.getenv("CALVIN_TEST_RUN_ROOT", str(REPO_ROOT / "estimate_scripts" / "test_runs"))
).resolve()
DEFAULT_MODEL_SCRIPTS_ROOT = Path(os.getenv("CALVIN_MODEL_SCRIPTS_ROOT", "/volumes/model_scripts")).resolve()

STEP3_RUN_ID_RE = re.compile(r"run_id=([a-zA-Z0-9_]+)")
RUN_ID_JSON_RE = re.compile(r'"run_id"\s*:\s*"([a-zA-Z0-9_]+)"')


def _utc_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _tail_lines(lines: List[str], limit: int = 300) -> str:
    return "\n".join(lines[-limit:])


def _wait_port(host: str, port: int, timeout_sec: float = 90.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            sock.connect((host, port))
            return True
        except Exception:
            time.sleep(0.3)
        finally:
            sock.close()
    return False


def _run_streaming_command(
    cmd: List[str],
    cwd: Path,
    log_sink: List[str],
    log_file: Path,
    stage: str,
) -> Generator[Tuple[str, Optional[str]], None, int]:
    _ensure_dir(log_file.parent)
    with log_file.open("a", encoding="utf-8") as file_out:
        file_out.write(f"\n===== {stage} =====\n")
        file_out.write("CMD: " + " ".join(cmd) + "\n")
        file_out.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        run_id: Optional[str] = None
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            file_out.write(line + "\n")
            file_out.flush()
            log_sink.append(f"[{stage}] {line}")
            match = STEP3_RUN_ID_RE.search(line)
            if match:
                run_id = match.group(1)
            else:
                match_json = RUN_ID_JSON_RE.search(line)
                if match_json:
                    run_id = match_json.group(1)
            yield line, run_id
        proc.wait()
        return proc.returncode


def _latest_step3_dir(run_root: Path) -> Optional[Path]:
    dirs = [x for x in run_root.glob("calvin_step3_*") if x.is_dir()]
    if not dirs:
        return None
    return sorted(dirs, key=lambda x: x.stat().st_mtime, reverse=True)[0]


def _collect_preview_images(step3_dir: Optional[Path], max_images: int = 9) -> List[str]:
    if step3_dir is None:
        return []
    frames_root = step3_dir / "frames"
    if not frames_root.exists():
        return []
    images = sorted(frames_root.glob("*/*/*.png"))
    if not images:
        return []
    images = images[-max_images:]
    return [str(x) for x in images]


def _build_episode_video(frames_dir: Path, output_path: Path, fps: int) -> bool:
    frames = sorted(frames_dir.glob("*.png"))
    if not frames:
        return False
    first = cv2.imread(str(frames[0]))
    if first is None:
        return False
    height, width = first.shape[:2]
    _ensure_dir(output_path.parent)
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), max(1, fps), (width, height))
    try:
        for frame_path in frames:
            frame = cv2.imread(str(frame_path))
            if frame is None:
                continue
            if frame.shape[0] != height or frame.shape[1] != width:
                frame = cv2.resize(frame, (width, height))
            writer.write(frame)
    finally:
        writer.release()
    return output_path.exists()


def _build_preview_videos(step3_dir: Optional[Path], videos_root: Path, fps: int, max_videos: int = 3) -> List[str]:
    if step3_dir is None:
        return []
    frames_root = step3_dir / "frames"
    if not frames_root.exists():
        return []
    episode_dirs = sorted(frames_root.glob("*/*"))
    if not episode_dirs:
        return []
    out: List[str] = []
    for episode_dir in episode_dirs[:max_videos]:
        model_id = episode_dir.parent.name
        episode_id = episode_dir.name
        out_path = videos_root / f"{model_id}_{episode_id}.mp4"
        if _build_episode_video(episode_dir, out_path, fps=fps):
            out.append(str(out_path))
    return out


def _zip_paths(zip_path: Path, paths: List[Path]) -> None:
    _ensure_dir(zip_path.parent)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in paths:
            if not item.exists():
                continue
            if item.is_file():
                archive.write(item, arcname=item.name)
                continue
            for fp in item.rglob("*"):
                if fp.is_file():
                    arcname = str(fp.relative_to(item.parent))
                    archive.write(fp, arcname=arcname)


def _path_status_line(title: str, path_str: str) -> str:
    p = Path(path_str).resolve()
    exists = p.exists()
    mark = "OK" if exists else "MISS"
    return f"- `{title}`: `{p}` [{mark}]"


def build_paths_help(
    calvin_root: str,
    dataset_path: str,
    protocol_root: str,
    contracts_root: str,
    run_root: str,
    test_run_root: str,
    model_scripts_root: str,
) -> str:
    lines = [
        "### Path Check",
        _path_status_line("calvin_root", calvin_root),
        _path_status_line("dataset_path", dataset_path),
        _path_status_line("protocol_root", protocol_root),
        _path_status_line("contracts_root", contracts_root),
        _path_status_line("run_root", run_root),
        _path_status_line("test_run_root", test_run_root),
        _path_status_line("model_scripts_root", model_scripts_root),
    ]
    return "\n".join(lines)


def auto_fill_defaults(
    calvin_root: str,
    dataset_path: str,
    protocol_root: str,
    contracts_root: str,
    run_root: str,
    test_run_root: str,
    model_scripts_root: str,
) -> Tuple[str, str, str, str, str, str, str, str]:
    calvin = calvin_root.strip() or str(DEFAULT_CALVIN_ROOT)
    dataset = dataset_path.strip() or str(DEFAULT_DATASET_PATH)
    protocol = protocol_root.strip() or str(DEFAULT_PROTOCOL_ROOT)
    contracts = contracts_root.strip() or str(Path(protocol) / "contracts")
    runs = run_root.strip() or str(DEFAULT_RUN_ROOT)
    tests = test_run_root.strip() or str(DEFAULT_TEST_RUN_ROOT)
    model_scripts = model_scripts_root.strip() or str(DEFAULT_MODEL_SCRIPTS_ROOT)
    info = build_paths_help(calvin, dataset, protocol, contracts, runs, tests, model_scripts)
    return calvin, dataset, protocol, contracts, runs, tests, model_scripts, info


def apply_preset(
    preset: str,
) -> Tuple[bool, bool, bool, bool, bool, int, int, bool, str]:
    # run_step1_if_missing, force_step1, run_step2_if_missing, force_step2, run_step3,
    # max_episodes, parallel_workers, save_frames, model_backend
    if preset == "Quick smoke (10 episodes)":
        return True, False, True, False, True, 10, 2, True, "mock_random"
    if preset == "Full benchmark (all episodes)":
        return True, False, True, False, True, 0, 2, True, "mock_random"
    if preset == "Prep only (step1+step2)":
        return True, False, True, False, False, 0, 1, False, "mock_random"
    return True, False, True, False, True, 10, 2, True, "mock_random"


def run_pipeline_stream(
    calvin_root: str,
    dataset_path: str,
    protocol_root: str,
    contracts_root: str,
    run_root: str,
    test_run_root: str,
    model_scripts_root: str,
    seed: int,
    official_total: int,
    selected_total: int,
    track: str,
    split: str,
    run_step1_if_missing: bool,
    force_step1: bool,
    run_step2_if_missing: bool,
    force_step2: bool,
    run_step3: bool,
    model_id: str,
    model_family: str,
    model_backend: str,
    model_host: str,
    model_port: int,
    model_timeout_sec: float,
    python_model_spec: str,
    python_model_kwargs: str,
    model_launch_command: str,
    parallel_workers: int,
    benchmark_size: int,
    max_episodes: int,
    save_frames: bool,
    allow_subtask_skip: bool,
    allow_incompatible_conditions: bool,
    show_gui: bool,
    video_fps: int,
) -> Generator[Tuple[str, str, List[str], List[str], Optional[str]], None, None]:
    status = "Idle"
    lines: List[str] = []
    preview_images: List[str] = []
    preview_videos: List[str] = []
    zip_file: Optional[str] = None

    def emit() -> Tuple[str, str, List[str], List[str], Optional[str]]:
        return status, _tail_lines(lines), preview_images, preview_videos, zip_file

    calvin_root_path = Path(calvin_root).resolve()
    dataset_path_path = Path(dataset_path).resolve()
    protocol_root_path = Path(protocol_root).resolve()
    contracts_root_path = Path(contracts_root).resolve()
    run_root_path = Path(run_root).resolve()
    test_run_root_path = Path(test_run_root).resolve()
    model_scripts_root_path = Path(model_scripts_root).resolve()

    job_id = f"ui_{_utc_tag()}"
    job_dir = run_root_path / "ui_jobs" / job_id
    job_log = job_dir / "job.log"
    _ensure_dir(job_dir)
    _ensure_dir(run_root_path)
    _ensure_dir(test_run_root_path)

    lines.append(f"[job] id={job_id}")
    lines.append(f"[job] repo_root={REPO_ROOT}")
    lines.append(f"[job] model_scripts_root={model_scripts_root_path}")
    yield emit()

    step3_run_id: Optional[str] = None
    step3_run_dir: Optional[Path] = None
    model_proc: Optional[subprocess.Popen] = None

    try:
        if not calvin_root_path.exists():
            status = "Error: calvin_root not found"
            lines.append(f"[error] missing calvin_root: {calvin_root_path}")
            yield emit()
            return

        if run_step3 and not dataset_path_path.exists():
            status = "Error: dataset_path not found"
            lines.append(f"[error] missing dataset_path: {dataset_path_path}")
            yield emit()
            return

        if run_step3 and model_backend == "python" and not python_model_spec.strip():
            status = "Error: python backend requires python_model_spec"
            lines.append("[error] python model backend selected but python_model_spec is empty")
            yield emit()
            return

        try:
            kwargs_obj = json.loads(python_model_kwargs.strip() or "{}")
            if not isinstance(kwargs_obj, dict):
                raise ValueError("python_model_kwargs must be a JSON object")
        except Exception as exc:
            status = "Error: invalid python_model_kwargs"
            lines.append(f"[error] python_model_kwargs parse error: {exc}")
            yield emit()
            return

        if model_launch_command.strip():
            status = "Starting model launch command"
            lines.append(f"[model] launch cmd: {model_launch_command}")
            yield emit()
            model_cmd = shlex.split(model_launch_command)
            model_log = job_dir / "model.log"
            with model_log.open("a", encoding="utf-8") as model_stream:
                model_proc = subprocess.Popen(
                    model_cmd,
                    cwd=str(REPO_ROOT),
                    stdout=model_stream,
                    stderr=model_stream,
                    start_new_session=True,
                )
            if model_backend == "http":
                if not _wait_port(model_host, int(model_port), timeout_sec=120.0):
                    status = "Error: model HTTP endpoint not reachable"
                    lines.append(f"[error] endpoint timeout: {model_host}:{model_port}")
                    yield emit()
                    return
                lines.append(f"[model] endpoint ready: {model_host}:{model_port}")
                yield emit()

        task_manifest = protocol_root_path / "manifest" / "task_manifest.json"
        benchmark_manifest = protocol_root_path / "manifest" / "benchmark_manifest.json"
        need_step1 = force_step1 or not (task_manifest.exists() and benchmark_manifest.exists())

        if run_step1_if_missing and need_step1:
            status = "Step 1: deterministic sample generation"
            yield emit()
            step1_cmd = [
                "python",
                "estimate_scripts/main.py",
                "--step",
                "1",
                "--calvin-root",
                str(calvin_root_path),
                "--output-root",
                str(protocol_root_path),
                "--seed",
                str(int(seed)),
                "--official-total",
                str(int(official_total)),
                "--selected-total",
                str(int(selected_total)),
                "--track",
                str(track),
                "--split",
                str(split),
            ]
            stream = _run_streaming_command(step1_cmd, REPO_ROOT, lines, job_log, "STEP1")
            rc1 = 0
            while True:
                try:
                    next(stream)
                    yield emit()
                except StopIteration as stop:
                    rc1 = stop.value if isinstance(stop.value, int) else 1
                    break
            if rc1 not in (0, None):
                status = "Error: step1 failed"
                yield emit()
                return
        else:
            lines.append("[step1] skipped")
            yield emit()

        scenario_manifest = contracts_root_path / "scenario_contract_manifest.json"
        need_step2 = force_step2 or not scenario_manifest.exists()
        if run_step2_if_missing and need_step2:
            status = "Step 2: protocol contracts generation"
            yield emit()
            step2_cmd = [
                "python",
                "estimate_scripts/main.py",
                "--step",
                "2",
                "--protocol-root",
                str(protocol_root_path),
                "--contracts-output-root",
                str(contracts_root_path),
            ]
            if force_step2:
                step2_cmd.append("--contracts-force")
            stream = _run_streaming_command(step2_cmd, REPO_ROOT, lines, job_log, "STEP2")
            rc2 = 0
            while True:
                try:
                    next(stream)
                    yield emit()
                except StopIteration as stop:
                    rc2 = stop.value if isinstance(stop.value, int) else 1
                    break
            if rc2 not in (0, None):
                status = "Error: step2 failed"
                yield emit()
                return
        else:
            lines.append("[step2] skipped")
            yield emit()

        if run_step3:
            status = "Step 3: benchmark runtime"
            yield emit()
            step3_cmd = [
                "python",
                "estimate_scripts/main.py",
                "--step",
                "3",
                "--calvin-root",
                str(calvin_root_path),
                "--dataset-path",
                str(dataset_path_path),
                "--protocol-root",
                str(protocol_root_path),
                "--contracts-root",
                str(contracts_root_path),
                "--run-root",
                str(run_root_path),
                "--model-id",
                str(model_id),
                "--model-family",
                str(model_family),
                "--model-backend",
                str(model_backend),
                "--model-host",
                str(model_host),
                "--model-port",
                str(int(model_port)),
                "--model-timeout-sec",
                str(float(model_timeout_sec)),
                "--parallel-workers",
                str(int(parallel_workers)),
                "--benchmark-size",
                str(int(benchmark_size)),
                "--max-episodes",
                str(int(max_episodes)),
                "--python-model-kwargs",
                json.dumps(kwargs_obj, ensure_ascii=False),
            ]
            if model_backend == "python" and python_model_spec.strip():
                step3_cmd.extend(["--python-model-spec", python_model_spec.strip()])
            if save_frames:
                step3_cmd.append("--save-frames")
            if allow_subtask_skip:
                step3_cmd.append("--allow-subtask-skip")
            if allow_incompatible_conditions:
                step3_cmd.append("--allow-incompatible-conditions")
            if show_gui:
                step3_cmd.append("--show-gui")

            stream = _run_streaming_command(step3_cmd, REPO_ROOT, lines, job_log, "STEP3")
            last_preview_ts = 0.0
            rc3 = 0
            while True:
                try:
                    _line, maybe_run_id = next(stream)
                    if maybe_run_id and maybe_run_id != step3_run_id:
                        step3_run_id = maybe_run_id
                        step3_run_dir = run_root_path / step3_run_id
                        lines.append(f"[step3] detected run_dir: {step3_run_dir}")
                    now = time.time()
                    if now - last_preview_ts >= 2.0:
                        preview_images = _collect_preview_images(step3_run_dir, max_images=9)
                        last_preview_ts = now
                    yield emit()
                except StopIteration as stop:
                    rc3 = stop.value if isinstance(stop.value, int) else 1
                    break

            if rc3 not in (0, None):
                status = "Error: step3 failed"
                yield emit()
                return

            if step3_run_dir is None:
                step3_run_dir = _latest_step3_dir(run_root_path)
            if step3_run_dir is None:
                status = "Error: could not detect step3 run directory"
                lines.append("[error] run_dir not found")
                yield emit()
                return

            status = "Building previews"
            preview_images = _collect_preview_images(step3_run_dir, max_images=9)
            preview_videos = _build_preview_videos(
                step3_dir=step3_run_dir,
                videos_root=job_dir / "videos",
                fps=int(video_fps),
                max_videos=3,
            )
            lines.append(f"[post] preview videos: {len(preview_videos)}")
            yield emit()

            status = "Packing ZIP"
            zip_path = job_dir / f"benchmark_{job_id}.zip"
            _zip_paths(zip_path=zip_path, paths=[job_dir, step3_run_dir])
            zip_file = str(zip_path)
            lines.append(f"[post] zip: {zip_file}")
            status = "Done"
            yield emit()
            return

        status = "Packing prep artifacts"
        zip_path = job_dir / f"prep_{job_id}.zip"
        _zip_paths(zip_path=zip_path, paths=[job_dir, protocol_root_path, contracts_root_path])
        zip_file = str(zip_path)
        lines.append(f"[post] zip: {zip_file}")
        status = "Done (prep only)"
        yield emit()
    except Exception as exc:
        status = "Runtime exception"
        lines.append(f"[exception] {type(exc).__name__}: {exc}")
        yield emit()
    finally:
        if model_proc is not None:
            try:
                model_proc.terminate()
                model_proc.wait(timeout=5)
            except Exception:
                try:
                    model_proc.kill()
                except Exception:
                    pass


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="CALVIN Benchmark UI") as app:
        gr.Markdown("## CALVIN Benchmark UI")
        gr.Markdown(
            "UI runs step1/step2/step3 for `calvin_bench/estimate_scripts`, "
            "streams live logs, shows frame previews, and produces a ZIP with artifacts."
        )

        with gr.Accordion("1) Infrastructure and Paths", open=True):
            with gr.Row():
                calvin_root = gr.Textbox(label="calvin_root", value=str(DEFAULT_CALVIN_ROOT))
                dataset_path = gr.Textbox(label="dataset_path", value=str(DEFAULT_DATASET_PATH))
            with gr.Row():
                protocol_root = gr.Textbox(label="protocol_root", value=str(DEFAULT_PROTOCOL_ROOT))
                contracts_root = gr.Textbox(label="contracts_root", value=str(DEFAULT_CONTRACTS_ROOT))
            with gr.Row():
                run_root = gr.Textbox(label="run_root", value=str(DEFAULT_RUN_ROOT))
                test_run_root = gr.Textbox(label="test_run_root", value=str(DEFAULT_TEST_RUN_ROOT))
            model_scripts_root = gr.Textbox(label="model_scripts_root", value=str(DEFAULT_MODEL_SCRIPTS_ROOT))
            with gr.Row():
                auto_fill_btn = gr.Button("Auto-fill defaults")
                validate_paths_btn = gr.Button("Validate paths")
            paths_info = gr.Markdown(
                value=build_paths_help(
                    str(DEFAULT_CALVIN_ROOT),
                    str(DEFAULT_DATASET_PATH),
                    str(DEFAULT_PROTOCOL_ROOT),
                    str(DEFAULT_CONTRACTS_ROOT),
                    str(DEFAULT_RUN_ROOT),
                    str(DEFAULT_TEST_RUN_ROOT),
                    str(DEFAULT_MODEL_SCRIPTS_ROOT),
                )
            )

        with gr.Accordion("2) Step1 + Step2", open=True):
            with gr.Row():
                seed = gr.Number(label="seed", value=42, precision=0)
                official_total = gr.Number(label="official_total", value=1000, precision=0)
                selected_total = gr.Number(label="selected_total", value=1000, precision=0)
            with gr.Row():
                track = gr.Textbox(label="track", value="unified_ranking")
                split = gr.Textbox(label="split", value="validation")
            with gr.Row():
                run_step1_if_missing = gr.Checkbox(label="run step1 if bundle is missing", value=True)
                force_step1 = gr.Checkbox(label="force step1 rebuild", value=False)
            with gr.Row():
                run_step2_if_missing = gr.Checkbox(label="run step2 if contracts are missing", value=True)
                force_step2 = gr.Checkbox(label="force step2 rebuild", value=False)

        with gr.Accordion("3) Model and Step3 runtime", open=True):
            with gr.Row():
                model_id = gr.Textbox(label="model_id", value="ui_model")
                model_family = gr.Textbox(label="model_family", value="unknown")
                model_backend = gr.Dropdown(label="model_backend", choices=["mock_random", "http", "python"], value="mock_random")
            with gr.Row():
                model_host = gr.Textbox(label="model_host", value="127.0.0.1")
                model_port = gr.Number(label="model_port", value=9000, precision=0)
                model_timeout_sec = gr.Number(label="model_timeout_sec", value=30.0)
            with gr.Row():
                python_model_spec = gr.Textbox(label="python_model_spec", value="", placeholder="/volumes/model_scripts/my_model.py:MyModel")
                python_model_kwargs = gr.Textbox(label="python_model_kwargs", value="{}")
            model_launch_command = gr.Textbox(
                label="model launch command (optional)",
                value="",
                placeholder="python /volumes/model_scripts/model_server.py --host 0.0.0.0 --port 9000",
            )
            with gr.Row():
                run_step3 = gr.Checkbox(label="run step3", value=True)
                parallel_workers = gr.Number(label="parallel_workers", value=2, precision=0)
                benchmark_size = gr.Number(label="benchmark_size (0 = use max_episodes/all)", value=0, precision=0)
                max_episodes = gr.Number(label="max_episodes (0 = all)", value=10, precision=0)
            with gr.Row():
                save_frames = gr.Checkbox(label="save_frames", value=True)
                allow_subtask_skip = gr.Checkbox(label="allow_subtask_skip", value=True)
                allow_incompatible_conditions = gr.Checkbox(label="allow_incompatible_conditions", value=True)
                show_gui = gr.Checkbox(label="show_gui", value=False)
            video_fps = gr.Number(label="preview video fps", value=5, precision=0)

        with gr.Accordion("4) Presets", open=False):
            with gr.Row():
                preset = gr.Dropdown(
                    label="preset",
                    choices=[
                        "Quick smoke (10 episodes)",
                        "Full benchmark (all episodes)",
                        "Prep only (step1+step2)",
                    ],
                    value="Quick smoke (10 episodes)",
                )
                apply_preset_btn = gr.Button("Apply preset")

        run_btn = gr.Button("Run benchmark", variant="primary")

        with gr.Accordion("5) Progress and Artifacts", open=True):
            status_md = gr.Markdown(label="status")
            live_logs = gr.Textbox(label="live logs", lines=18, max_lines=40)
            preview_gallery = gr.Gallery(label="latest frames", columns=3, rows=3, height=420, object_fit="contain")
            preview_videos = gr.File(label="preview videos", file_count="multiple")
            zip_file = gr.File(label="result zip", file_count="single")

        auto_fill_btn.click(
            fn=auto_fill_defaults,
            inputs=[
                calvin_root,
                dataset_path,
                protocol_root,
                contracts_root,
                run_root,
                test_run_root,
                model_scripts_root,
            ],
            outputs=[
                calvin_root,
                dataset_path,
                protocol_root,
                contracts_root,
                run_root,
                test_run_root,
                model_scripts_root,
                paths_info,
            ],
        )
        validate_paths_btn.click(
            fn=build_paths_help,
            inputs=[
                calvin_root,
                dataset_path,
                protocol_root,
                contracts_root,
                run_root,
                test_run_root,
                model_scripts_root,
            ],
            outputs=[paths_info],
        )
        apply_preset_btn.click(
            fn=apply_preset,
            inputs=[preset],
            outputs=[
                run_step1_if_missing,
                force_step1,
                run_step2_if_missing,
                force_step2,
                run_step3,
                max_episodes,
                parallel_workers,
                save_frames,
                model_backend,
            ],
        )

        run_btn.click(
            fn=run_pipeline_stream,
            inputs=[
                calvin_root,
                dataset_path,
                protocol_root,
                contracts_root,
                run_root,
                test_run_root,
                model_scripts_root,
                seed,
                official_total,
                selected_total,
                track,
                split,
                run_step1_if_missing,
                force_step1,
                run_step2_if_missing,
                force_step2,
                run_step3,
                model_id,
                model_family,
                model_backend,
                model_host,
                model_port,
                model_timeout_sec,
                python_model_spec,
                python_model_kwargs,
                model_launch_command,
                parallel_workers,
                benchmark_size,
                max_episodes,
                save_frames,
                allow_subtask_skip,
                allow_incompatible_conditions,
                show_gui,
                video_fps,
            ],
            outputs=[status_md, live_logs, preview_gallery, preview_videos, zip_file],
            queue=True,
        )
    return app


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gradio UI for CALVIN benchmark.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--share", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    app = build_ui()
    app.queue(default_concurrency_limit=1).launch(server_name=args.host, server_port=args.port, share=args.share)


if __name__ == "__main__":
    main()
