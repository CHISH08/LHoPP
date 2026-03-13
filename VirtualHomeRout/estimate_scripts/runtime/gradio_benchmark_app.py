import argparse
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
DEFAULT_RUN_ROOT = REPO_ROOT / "estimate_scripts" / "runs"
DEFAULT_PROTOCOL_ROOT = REPO_ROOT / "estimate_scripts" / "protocol_bundle"
DEFAULT_CONTRACTS_ROOT = DEFAULT_PROTOCOL_ROOT / "contracts"
DEFAULT_TASKS_ROOT = DEFAULT_PROTOCOL_ROOT / "data" / "tasks"
DEFAULT_DATASET_ROOT = REPO_ROOT / "virtualhome" / "virtualhome" / "dataset" / "programs_processed_precond_nograb_morepreconds" / "executable_programs"
DEFAULT_UNITY_EXE = REPO_ROOT / "dataset" / "linux_exec.v2.3.0" / "linux_exec.v2.3.0.x86_64"

STEP4_RUN_ID_RE = re.compile(r"run_id=([a-zA-Z0-9_]+)")


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
    status: str,
) -> Generator[Tuple[str, Optional[str]], None, int]:
    _ensure_dir(log_file.parent)
    with log_file.open("a", encoding="utf-8") as file_out:
        file_out.write(f"\n===== {status} =====\n")
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
        step4_run_id: Optional[str] = None
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            file_out.write(line + "\n")
            file_out.flush()
            log_sink.append(f"[{status}] {line}")
            match = STEP4_RUN_ID_RE.search(line)
            if match:
                step4_run_id = match.group(1)
            yield line, step4_run_id
        proc.wait()
        return proc.returncode


def _latest_step4_dir(run_root: Path) -> Optional[Path]:
    dirs = [x for x in run_root.glob("vh_step4_*") if x.is_dir()]
    if not dirs:
        return None
    return sorted(dirs, key=lambda x: x.stat().st_mtime, reverse=True)[0]


def _collect_preview_images(step4_dir: Optional[Path], max_images: int = 9) -> List[str]:
    if step4_dir is None:
        return []
    frames_root = step4_dir / "frames"
    if not frames_root.exists():
        return []
    images = sorted(frames_root.glob("*/*/*.png"))
    if not images:
        return []
    # evenly sample latest part to make progress visible
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


def _build_preview_videos(step4_dir: Optional[Path], videos_root: Path, fps: int, max_videos: int = 3) -> List[str]:
    if step4_dir is None:
        return []
    frames_root = step4_dir / "frames"
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


def _missing(path: str) -> bool:
    return not Path(path).exists()


def _path_status_line(title: str, path_str: str) -> str:
    p = Path(path_str).resolve()
    exists = p.exists()
    mark = "OK" if exists else "MISS"
    return f"- `{title}`: `{p}` [{mark}]"


def build_paths_help(
    dataset_root: str,
    unity_exe: str,
    protocol_root: str,
    contracts_root: str,
    tasks_root: str,
    run_root: str,
) -> str:
    lines = [
        "### Проверка путей",
        _path_status_line("dataset_root", dataset_root),
        _path_status_line("unity_exe", unity_exe),
        _path_status_line("protocol_root", protocol_root),
        _path_status_line("contracts_root", contracts_root),
        _path_status_line("tasks_root", tasks_root),
        _path_status_line("run_root", run_root),
    ]
    return "\n".join(lines)


def auto_fill_defaults(
    dataset_root: str,
    unity_exe: str,
    protocol_root: str,
    contracts_root: str,
    tasks_root: str,
    run_root: str,
) -> Tuple[str, str, str, str, str, str, str]:
    dataset = dataset_root.strip() or os.getenv("VH_DATASET_ROOT", str(DEFAULT_DATASET_ROOT))
    unity = unity_exe.strip() or os.getenv("VH_UNITY_EXE", str(DEFAULT_UNITY_EXE))
    protocol = protocol_root.strip() or str(DEFAULT_PROTOCOL_ROOT)
    contracts = contracts_root.strip() or str(Path(protocol) / "contracts")
    tasks = tasks_root.strip() or str(Path(protocol) / "data" / "tasks")
    runs = run_root.strip() or str(DEFAULT_RUN_ROOT)
    info = build_paths_help(dataset, unity, protocol, contracts, tasks, runs)
    return dataset, unity, protocol, contracts, tasks, runs, info


def apply_preset(preset: str) -> Tuple[bool, bool, bool, bool, int, int, bool]:
    # run_step1_if_missing, force_step1, run_step3_if_missing, force_step3, max_episodes, parallel_workers, save_frames
    if preset == "Быстрый smoke (2 эпизода)":
        return True, False, True, False, 2, 1, True
    if preset == "Полный прогон (все эпизоды)":
        return True, False, True, False, 0, 2, True
    if preset == "Только step4 на готовом bundle":
        return False, False, False, False, 0, 2, True
    return True, False, True, False, 0, 1, True


def run_pipeline_stream(
    dataset_root: str,
    protocol_root: str,
    contracts_root: str,
    tasks_root: str,
    run_root: str,
    unity_exe: str,
    model_id: str,
    model_family: str,
    model_host: str,
    model_port: int,
    model_timeout_sec: float,
    model_launch_command: str,
    seed: int,
    per_stratum: int,
    track: str,
    parallel_workers: int,
    base_port: int,
    time_scale: float,
    skip_animation: bool,
    image_width: int,
    image_height: int,
    max_episodes: int,
    save_frames: bool,
    frame_camera_index: int,
    frame_mode: str,
    video_fps: int,
    run_step1_if_missing: bool,
    force_step1: bool,
    run_step3_if_missing: bool,
    force_step3: bool,
) -> Generator[Tuple[str, str, List[str], List[str], Optional[str]], None, None]:
    status = "Ожидание запуска..."
    lines: List[str] = []
    preview_images: List[str] = []
    preview_videos: List[str] = []
    zip_file: Optional[str] = None

    def emit() -> Tuple[str, str, List[str], List[str], Optional[str]]:
        return status, _tail_lines(lines), preview_images, preview_videos, zip_file

    run_root_path = Path((run_root or str(DEFAULT_RUN_ROOT))).resolve()
    protocol_root_path = Path((protocol_root or str(DEFAULT_PROTOCOL_ROOT))).resolve()
    contracts_root_path = Path((contracts_root or str(protocol_root_path / "contracts"))).resolve()
    tasks_root_path = Path((tasks_root or str(protocol_root_path / "data" / "tasks"))).resolve()
    dataset_root_path = Path((dataset_root or str(DEFAULT_DATASET_ROOT))).resolve()
    unity_exe_path = Path((unity_exe or os.getenv("VH_UNITY_EXE", str(DEFAULT_UNITY_EXE)))).resolve()
    job_id = f"ui_{_utc_tag()}"
    job_dir = run_root_path / "ui_jobs" / job_id
    job_log = job_dir / "job.log"
    _ensure_dir(job_dir)

    lines.append(f"[job] id={job_id}")
    lines.append(f"[job] repo={REPO_ROOT}")
    lines.append(f"[job] run_root={run_root_path}")
    yield emit()

    model_proc: Optional[subprocess.Popen] = None
    step4_run_dir: Optional[Path] = None
    step4_run_id: Optional[str] = None

    try:
        if _missing(str(unity_exe_path)):
            status = "Ошибка: Unity executable не найден"
            lines.append(f"[error] unity_exe not found: {unity_exe_path}")
            yield emit()
            return
        if run_step1_if_missing and _missing(str(dataset_root_path)):
            status = "Ошибка: dataset root не найден"
            lines.append(f"[error] dataset_root not found: {dataset_root_path}")
            yield emit()
            return

        # Optional model launch
        if model_launch_command.strip():
            status = "Запуск модели..."
            lines.append(f"[model] launching: {model_launch_command}")
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
            if not _wait_port(model_host, int(model_port), timeout_sec=120.0):
                status = "Ошибка: модель не открыла порт"
                lines.append(f"[error] model endpoint not reachable: {model_host}:{model_port}")
                yield emit()
                return
            lines.append(f"[model] ready on {model_host}:{model_port}")
            yield emit()

        # Step 1 if needed
        task_manifest = protocol_root_path / "manifest" / "task_manifest.json"
        benchmark_manifest = protocol_root_path / "manifest" / "benchmark_manifest.json"
        need_step1 = force_step1 or not (task_manifest.exists() and benchmark_manifest.exists())
        if run_step1_if_missing and need_step1:
            status = "Шаг 1: сбор выборки задач..."
            yield emit()
            step1_cmd = [
                "python",
                "estimate_scripts/main.py",
                "--step",
                "1",
                "--dataset-root",
                str(dataset_root_path),
                "--output-root",
                str(protocol_root_path),
                "--seed",
                str(seed),
                "--per-stratum",
                str(per_stratum),
                "--track",
                str(track),
            ]
            rc = 0
            stream = _run_streaming_command(step1_cmd, REPO_ROOT, lines, job_log, "STEP1")
            while True:
                try:
                    _line, _run = next(stream)
                    yield emit()
                except StopIteration as stop:
                    rc = stop.value if isinstance(stop.value, int) else 1
                    break
            if rc not in (0, None):
                status = "Ошибка: шаг 1 завершился с ошибкой"
                yield emit()
                return
        else:
            lines.append("[step1] skipped (existing bundle)")
            yield emit()

        # Step 3 if needed
        scenario_manifest = contracts_root_path / "scenario_contract_manifest.json"
        need_step3 = force_step3 or not scenario_manifest.exists()
        if run_step3_if_missing and need_step3:
            status = "Шаг 3: генерация контрактов..."
            yield emit()
            step3_cmd = [
                "python",
                "estimate_scripts/main.py",
                "--step",
                "3",
                "--protocol-root",
                str(protocol_root_path),
                "--contracts-output-root",
                str(contracts_root_path),
            ]
            if force_step3:
                step3_cmd.append("--contracts-force")
            rc3 = 0
            stream = _run_streaming_command(step3_cmd, REPO_ROOT, lines, job_log, "STEP3")
            while True:
                try:
                    _line, _run = next(stream)
                    yield emit()
                except StopIteration as stop:
                    rc3 = stop.value if isinstance(stop.value, int) else 1
                    break
            if rc3 not in (0, None):
                status = "Ошибка: шаг 3 завершился с ошибкой"
                yield emit()
                return
        else:
            lines.append("[step3] skipped (existing contracts)")
            yield emit()

        status = "Шаг 4: запуск бенчмарка..."
        yield emit()
        step4_cmd = [
            "python",
            "estimate_scripts/main.py",
            "--step",
            "4",
            "--unity-exe",
            str(unity_exe_path),
            "--parallel-workers",
            str(parallel_workers),
            "--base-port",
            str(base_port),
            "--time-scale",
            str(time_scale),
            "--image-width",
            str(image_width),
            "--image-height",
            str(image_height),
            "--run-root",
            str(run_root_path),
            "--protocol-root",
            str(protocol_root_path),
            "--contracts-root",
            str(contracts_root_path),
            "--tasks-root",
            str(tasks_root_path),
            "--model-id",
            str(model_id),
            "--model-family",
            str(model_family),
            "--model-host",
            str(model_host),
            "--model-port",
            str(model_port),
            "--model-timeout-sec",
            str(model_timeout_sec),
            "--frame-camera-index",
            str(frame_camera_index),
            "--frame-mode",
            str(frame_mode),
            "--video-fps",
            str(video_fps),
            "--max-episodes",
            str(max_episodes),
        ]
        if skip_animation:
            step4_cmd.append("--skip-animation")
        if save_frames:
            step4_cmd.append("--save-frames")

        last_preview_ts = 0.0
        rc4 = 0
        stream = _run_streaming_command(step4_cmd, REPO_ROOT, lines, job_log, "STEP4")
        while True:
            try:
                _line, maybe_run_id = next(stream)
                if maybe_run_id and maybe_run_id != step4_run_id:
                    step4_run_id = maybe_run_id
                    step4_run_dir = run_root_path / step4_run_id
                    lines.append(f"[step4] detected run dir: {step4_run_dir}")
                now = time.time()
                if now - last_preview_ts >= 2.0:
                    preview_images = _collect_preview_images(step4_run_dir, max_images=9)
                    last_preview_ts = now
                yield emit()
            except StopIteration as stop:
                rc4 = stop.value if isinstance(stop.value, int) else 1
                break
        if rc4 not in (0, None):
            status = "Ошибка: шаг 4 завершился с ошибкой"
            yield emit()
            return

        if step4_run_dir is None:
            step4_run_dir = _latest_step4_dir(run_root_path)
        if step4_run_dir is None:
            status = "Ошибка: не найден run_dir шага 4"
            lines.append("[error] step4 run directory not found")
            yield emit()
            return

        status = "Формирование preview-видео..."
        preview_images = _collect_preview_images(step4_run_dir, max_images=9)
        preview_videos = _build_preview_videos(
            step4_run_dir=step4_run_dir,
            videos_root=job_dir / "videos",
            fps=video_fps,
            max_videos=3,
        )
        lines.append(f"[post] videos generated: {len(preview_videos)}")
        yield emit()

        status = "Упаковка ZIP..."
        zip_path = job_dir / f"benchmark_{job_id}.zip"
        _zip_paths(
            zip_path=zip_path,
            paths=[job_dir, step4_run_dir],
        )
        zip_file = str(zip_path)
        lines.append(f"[post] zip ready: {zip_file}")
        status = "Готово"
        yield emit()
    except Exception as exc:
        status = "Ошибка выполнения"
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
    with gr.Blocks(title="VirtualHome Benchmark UI") as app:
        gr.Markdown("## VirtualHome Benchmark UI")
        gr.Markdown("Один экран для запуска бенчмарка: автоподготовка шагов 1/3, step4, live-прогресс, preview и ZIP.")

        with gr.Accordion("1) Инфраструктура и пути", open=True):
            gr.Markdown(
                "Этот блок определяет, где брать Unity/датасет и куда сохранять результаты. "
                "Кнопка `Автозаполнение` заполнит пустые поля дефолтами."
            )
            with gr.Row():
                dataset_root = gr.Textbox(label="Dataset Root (для step1)", value=os.getenv("VH_DATASET_ROOT", str(DEFAULT_DATASET_ROOT)))
                unity_exe = gr.Textbox(label="Unity Executable", value=os.getenv("VH_UNITY_EXE", str(DEFAULT_UNITY_EXE)))
            with gr.Row():
                protocol_root = gr.Textbox(label="Protocol Root", value=str(DEFAULT_PROTOCOL_ROOT))
                contracts_root = gr.Textbox(label="Contracts Root", value=str(DEFAULT_CONTRACTS_ROOT))
                tasks_root = gr.Textbox(label="Tasks Root", value=str(DEFAULT_TASKS_ROOT))
                run_root = gr.Textbox(label="Run Root", value=str(DEFAULT_RUN_ROOT))
            with gr.Row():
                auto_fill_btn = gr.Button("Автозаполнение путей")
                validate_paths_btn = gr.Button("Проверить пути")
            paths_info = gr.Markdown(
                value=build_paths_help(
                    os.getenv("VH_DATASET_ROOT", str(DEFAULT_DATASET_ROOT)),
                    os.getenv("VH_UNITY_EXE", str(DEFAULT_UNITY_EXE)),
                    str(DEFAULT_PROTOCOL_ROOT),
                    str(DEFAULT_CONTRACTS_ROOT),
                    str(DEFAULT_TASKS_ROOT),
                    str(DEFAULT_RUN_ROOT),
                )
            )

        with gr.Accordion("2) Подготовка (шаг 1 и шаг 3)", open=False):
            gr.Markdown(
                "Если bundle/контракты уже готовы, отключите автозапуск шагов. "
                "Для воспроизводимости обычно `force_*` выключены."
            )
            with gr.Row():
                seed = gr.Number(label="seed", value=42, precision=0)
                per_stratum = gr.Number(label="per_stratum", value=30, precision=0)
                track = gr.Textbox(label="track", value="unified_ranking")
            with gr.Row():
                run_step1_if_missing = gr.Checkbox(label="Запускать step1 при отсутствии bundle", value=True)
                force_step1 = gr.Checkbox(label="Всегда пересобирать step1", value=False)
                run_step3_if_missing = gr.Checkbox(label="Запускать step3 при отсутствии contracts", value=True)
                force_step3 = gr.Checkbox(label="Всегда пересобирать step3", value=False)

        with gr.Accordion("3) Модель (HTTP)", open=True):
            gr.Markdown(
                "Можно либо подключиться к уже поднятой модели (`host/port`), "
                "либо указать команду автозапуска model server."
            )
            with gr.Row():
                model_id = gr.Textbox(label="model_id", value="ui_model")
                model_family = gr.Textbox(label="model_family", value="llm")
                model_host = gr.Textbox(label="model_host", value="127.0.0.1")
                model_port = gr.Number(label="model_port", value=9000, precision=0)
                model_timeout_sec = gr.Number(label="model_timeout_sec", value=30.0)
            model_launch_command = gr.Textbox(
                label="model launch command (optional)",
                value="",
                placeholder="python /models/my_model_server.py --host 0.0.0.0 --port 9000",
            )

        with gr.Accordion("4) Параметры step4", open=True):
            gr.Markdown("Основные параметры исполнения сценариев в Unity.")
            with gr.Row():
                preset = gr.Dropdown(
                    label="Пресет",
                    choices=[
                        "Быстрый smoke (2 эпизода)",
                        "Полный прогон (все эпизоды)",
                        "Только step4 на готовом bundle",
                    ],
                    value="Быстрый smoke (2 эпизода)",
                )
                apply_preset_btn = gr.Button("Применить пресет")
            with gr.Row():
                parallel_workers = gr.Number(label="parallel_workers", value=1, precision=0)
                base_port = gr.Number(label="base_port", value=8090, precision=0)
                max_episodes = gr.Number(label="max_episodes (0=all)", value=2, precision=0)
                time_scale = gr.Number(label="time_scale", value=1.0)
            with gr.Row():
                skip_animation = gr.Checkbox(label="skip_animation", value=False)
                image_width = gr.Number(label="image_width", value=320, precision=0)
                image_height = gr.Number(label="image_height", value=240, precision=0)
                save_frames = gr.Checkbox(label="save_frames", value=True)
            with gr.Row():
                frame_camera_index = gr.Number(label="frame_camera_index", value=0, precision=0)
                frame_mode = gr.Dropdown(
                    label="frame_mode",
                    choices=["normal", "seg_inst", "seg_class", "depth", "flow", "albedo", "illumination", "surf_normals"],
                    value="normal",
                )
                video_fps = gr.Number(label="video_fps", value=5, precision=0)

        run_btn = gr.Button("Запустить бенчмарк", variant="primary")

        with gr.Accordion("5) Прогресс и результаты", open=True):
            gr.Markdown(
                "Статус обновляется в реальном времени. После завершения появится ZIP "
                "со всеми логами/артефактами и preview-видео."
            )
            status_md = gr.Markdown(label="status")
            live_logs = gr.Textbox(label="live logs", lines=18, max_lines=40)
            preview_gallery = gr.Gallery(label="latest frames", columns=3, rows=3, height=400, object_fit="contain")
            preview_videos = gr.File(label="preview videos", file_count="multiple")
            zip_file = gr.File(label="result zip", file_count="single")

        auto_fill_btn.click(
            fn=auto_fill_defaults,
            inputs=[dataset_root, unity_exe, protocol_root, contracts_root, tasks_root, run_root],
            outputs=[dataset_root, unity_exe, protocol_root, contracts_root, tasks_root, run_root, paths_info],
        )
        validate_paths_btn.click(
            fn=build_paths_help,
            inputs=[dataset_root, unity_exe, protocol_root, contracts_root, tasks_root, run_root],
            outputs=[paths_info],
        )
        apply_preset_btn.click(
            fn=apply_preset,
            inputs=[preset],
            outputs=[
                run_step1_if_missing,
                force_step1,
                run_step3_if_missing,
                force_step3,
                max_episodes,
                parallel_workers,
                save_frames,
            ],
        )

        run_btn.click(
            fn=run_pipeline_stream,
            inputs=[
                dataset_root,
                protocol_root,
                contracts_root,
                tasks_root,
                run_root,
                unity_exe,
                model_id,
                model_family,
                model_host,
                model_port,
                model_timeout_sec,
                model_launch_command,
                seed,
                per_stratum,
                track,
                parallel_workers,
                base_port,
                time_scale,
                skip_animation,
                image_width,
                image_height,
                max_episodes,
                save_frames,
                frame_camera_index,
                frame_mode,
                video_fps,
                run_step1_if_missing,
                force_step1,
                run_step3_if_missing,
                force_step3,
            ],
            outputs=[status_md, live_logs, preview_gallery, preview_videos, zip_file],
            queue=True,
        )
    return app


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gradio UI for VirtualHome benchmark.")
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
