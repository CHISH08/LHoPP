import argparse
import json
from pathlib import Path

from runtime.step4_runner import run_unity_benchmark_step4
from utils.build_protocol_dataset import build_protocol_dataset
from utils.build_scenario_contracts import build_scenario_contracts
from unity_scripts.env_probe import run_unity_bootstrap


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate scripts pipeline entrypoint."
    )
    parser.add_argument(
        "--step",
        type=int,
        default=1,
        choices=[1, 2, 3, 4],
        help=(
            "Pipeline step number. "
            "Step 1: deterministic dataset bundle build. "
            "Step 2: Unity bootstrap (infra checks + standby). "
            "Step 3: machine-readable scenario contracts generation. "
            "Step 4: Unity benchmark runtime with model HTTP interaction."
        ),
    )
    parser.add_argument(
        "--dataset-root",
        default="virtualhome/virtualhome/dataset/programs_processed_precond_nograb_morepreconds/executable_programs",
        help="Path to source executable_programs.",
    )
    parser.add_argument(
        "--output-root",
        default="estimate_scripts/protocol_bundle",
        help="Output folder for selected tasks/manifests.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--per-stratum", type=int, default=30)
    parser.add_argument("--track", default="unified_ranking")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--unity-exe",
        default="dataset/windows_exec.v2.3.0/VirtualHome.exe",
        help="Path to Unity executable for step 2.",
    )
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=1,
        help="Number of Unity environments to start for step 2.",
    )
    parser.add_argument(
        "--base-port",
        type=int,
        default=8090,
        help="Base Unity HTTP port for step 2. Slot i uses base_port+i.",
    )
    parser.add_argument(
        "--scene-id",
        type=int,
        default=0,
        help="Unity scene id used for reset() during step 2.",
    )
    parser.add_argument(
        "--time-scale",
        type=float,
        default=1.0,
        help="Unity render_script time_scale for step 2 probe action.",
    )
    parser.add_argument(
        "--skip-animation",
        action="store_true",
        help="Use skip_animation=True for step 2 probe action.",
    )
    parser.add_argument(
        "--image-width",
        type=int,
        default=320,
        help="Probe camera image width for step 2.",
    )
    parser.add_argument(
        "--image-height",
        type=int,
        default=240,
        help="Probe camera image height for step 2.",
    )
    parser.add_argument(
        "--run-root",
        default="estimate_scripts/runs",
        help="Root folder for step 2 run artifacts.",
    )
    parser.add_argument(
        "--standby-seconds",
        type=int,
        default=0,
        help="Step 2 standby duration in seconds. 0 means wait until Ctrl+C.",
    )
    parser.add_argument(
        "--protocol-root",
        default="estimate_scripts/protocol_bundle",
        help="Protocol bundle root for step 3.",
    )
    parser.add_argument(
        "--contracts-output-root",
        default="estimate_scripts/protocol_bundle/contracts",
        help="Output path for step 3 generated contracts.",
    )
    parser.add_argument(
        "--contracts-force",
        action="store_true",
        help="Force regenerate step 3 contracts if files already exist.",
    )
    parser.add_argument(
        "--contracts-root",
        default="estimate_scripts/protocol_bundle/contracts",
        help="Path to step 3 contracts for step 4 runtime.",
    )
    parser.add_argument(
        "--tasks-root",
        default="estimate_scripts/protocol_bundle/data/tasks",
        help="Path to bundled task files from step 1.",
    )
    parser.add_argument(
        "--model-id",
        default="model_default",
        help="Model identifier for step 4 logs.",
    )
    parser.add_argument(
        "--model-family",
        default="unknown",
        help="Model family label for step 4 logs.",
    )
    parser.add_argument(
        "--model-host",
        default="127.0.0.1",
        help="Model HTTP host for step 4.",
    )
    parser.add_argument(
        "--model-port",
        type=int,
        default=9000,
        help="Model HTTP port for step 4.",
    )
    parser.add_argument(
        "--model-timeout-sec",
        type=float,
        default=30.0,
        help="Model HTTP timeout in seconds for step 4.",
    )
    parser.add_argument(
        "--save-frames",
        action="store_true",
        help="Save per-step frames in step 4.",
    )
    parser.add_argument(
        "--frame-camera-index",
        type=int,
        default=0,
        help="Camera index used for step 4 frame capture.",
    )
    parser.add_argument(
        "--frame-mode",
        default="normal",
        help="Camera mode for step 4 frame capture.",
    )
    parser.add_argument(
        "--video-fps",
        type=int,
        default=5,
        help="Target fps for downstream video stitching manifest.",
    )
    parser.add_argument(
        "--max-episodes",
        type=int,
        default=0,
        help="Step 4: limit number of episodes for smoke runs. 0 means all episodes.",
    )
    return parser


def run_step_1(args: argparse.Namespace) -> dict:
    return build_protocol_dataset(
        dataset_root=Path(args.dataset_root),
        output_root=Path(args.output_root),
        seed=args.seed,
        per_stratum=args.per_stratum,
        track=args.track,
        dry_run=args.dry_run,
    )


def run_step_2(args: argparse.Namespace) -> dict:
    return run_unity_bootstrap(
        unity_exe=Path(args.unity_exe),
        run_root=Path(args.run_root),
        parallel_workers=args.parallel_workers,
        base_port=args.base_port,
        scene_id=args.scene_id,
        time_scale=args.time_scale,
        skip_animation=args.skip_animation,
        image_width=args.image_width,
        image_height=args.image_height,
        standby_seconds=args.standby_seconds,
    )


def run_step_3(args: argparse.Namespace) -> dict:
    return build_scenario_contracts(
        protocol_root=Path(args.protocol_root),
        output_root=Path(args.contracts_output_root),
        force=args.contracts_force,
    )


def run_step_4(args: argparse.Namespace) -> dict:
    return run_unity_benchmark_step4(
        unity_exe=Path(args.unity_exe),
        run_root=Path(args.run_root),
        parallel_workers=args.parallel_workers,
        base_port=args.base_port,
        time_scale=args.time_scale,
        skip_animation=args.skip_animation,
        image_width=args.image_width,
        image_height=args.image_height,
        model_id=args.model_id,
        model_family=args.model_family,
        model_host=args.model_host,
        model_port=args.model_port,
        model_timeout_sec=args.model_timeout_sec,
        protocol_root=Path(args.protocol_root),
        contracts_root=Path(args.contracts_root),
        tasks_root=Path(args.tasks_root),
        save_frames=args.save_frames,
        frame_camera_index=args.frame_camera_index,
        frame_mode=args.frame_mode,
        video_fps=args.video_fps,
        max_episodes=args.max_episodes,
    )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.step == 1:
        summary = run_step_1(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if args.step == 2:
        summary = run_step_2(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if args.step == 3:
        summary = run_step_3(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if args.step == 4:
        summary = run_step_4(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    raise RuntimeError(f"Unsupported step: {args.step}")


if __name__ == "__main__":
    main()
