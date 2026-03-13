import argparse
import json
from pathlib import Path

from runtime.step3_runner import run_calvin_benchmark_step3
from utils.build_protocol_dataset import build_protocol_dataset
from utils.build_scenario_contracts import build_scenario_contracts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "CALVIN estimate_scripts pipeline entrypoint. "
            "Step 1 builds deterministic scenario sample bundle. "
            "Step 2 builds machine-readable scenario contracts. "
            "Step 3 runs benchmark runtime with model interaction."
        )
    )
    parser.add_argument(
        "--step",
        type=int,
        default=1,
        choices=[1, 2, 3],
        help=(
            "Pipeline step number. "
            "Step 1: deterministic sample bundle. "
            "Step 2: scenario contracts generation. "
            "Step 3: benchmark runtime execution."
        ),
    )
    parser.add_argument(
        "--calvin-root",
        default="calvin_bench/calvin",
        help="Path to local CALVIN repo root.",
    )
    parser.add_argument(
        "--output-root",
        default="calvin_bench/estimate_scripts/protocol_bundle",
        help="Where to write selected scenarios and manifests.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic selection seed for scenario sampling.",
    )
    parser.add_argument(
        "--official-total",
        type=int,
        default=1000,
        help="How many official CALVIN sequences to generate before sampling.",
    )
    parser.add_argument(
        "--selected-total",
        type=int,
        default=1000,
        help="How many scenarios to keep in the deterministic bundle.",
    )
    parser.add_argument(
        "--track",
        default="unified_ranking",
        help="Benchmark track label written into benchmark manifest.",
    )
    parser.add_argument(
        "--split",
        default="validation",
        help="Dataset split label for manifests (metadata only in step 1).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute selection and print summary without writing artifacts.",
    )
    parser.add_argument(
        "--protocol-root",
        default="calvin_bench/estimate_scripts/protocol_bundle",
        help="Step 2 input root with step 1 manifests.",
    )
    parser.add_argument(
        "--contracts-output-root",
        default="calvin_bench/estimate_scripts/protocol_bundle/contracts",
        help="Step 2 output root for generated contracts.",
    )
    parser.add_argument(
        "--contracts-force",
        action="store_true",
        help="Step 2: overwrite existing contracts if they already exist.",
    )
    parser.add_argument(
        "--dataset-path",
        default="calvin_bench/calvin/dataset/task_D_D",
        help="Path to CALVIN dataset root (containing validation/). Used by step 3.",
    )
    parser.add_argument(
        "--contracts-root",
        default="calvin_bench/estimate_scripts/protocol_bundle/contracts",
        help="Path to step 2 contracts for step 3 runtime.",
    )
    parser.add_argument(
        "--run-root",
        default="calvin_bench/estimate_scripts/runs",
        help="Root folder for step 3 run outputs.",
    )
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=1,
        help="Step 3: number of parallel workers.",
    )
    parser.add_argument(
        "--benchmark-size",
        type=int,
        default=0,
        help="Step 3: max number of episodes to execute. 0 means no explicit limit.",
    )
    parser.add_argument(
        "--max-episodes",
        type=int,
        default=0,
        help="Step 3: alias limit for smoke runs. 0 means disabled.",
    )
    parser.add_argument(
        "--model-id",
        default="model_default",
        help="Step 3: model identifier for logs.",
    )
    parser.add_argument(
        "--model-family",
        default="unknown",
        help="Step 3: model family label for logs.",
    )
    parser.add_argument(
        "--model-backend",
        default="mock_random",
        choices=["http", "python", "mock_random"],
        help="Step 3: model backend type.",
    )
    parser.add_argument(
        "--model-host",
        default="127.0.0.1",
        help="Step 3: model host for HTTP backend.",
    )
    parser.add_argument(
        "--model-port",
        type=int,
        default=9000,
        help="Step 3: model port for HTTP backend.",
    )
    parser.add_argument(
        "--model-timeout-sec",
        type=float,
        default=30.0,
        help="Step 3: model timeout for HTTP backend.",
    )
    parser.add_argument(
        "--python-model-spec",
        default=None,
        help="Step 3: python backend model spec <module_or_file>:<ClassName>.",
    )
    parser.add_argument(
        "--python-model-kwargs",
        default="{}",
        help="Step 3: JSON object for python model init kwargs.",
    )
    parser.add_argument(
        "--save-frames",
        action="store_true",
        help="Step 3: save per-step frames.",
    )
    parser.add_argument(
        "--allow-subtask-skip",
        action="store_true",
        help="Step 3: allow moving to next subtask when per-subtask budget is exhausted.",
    )
    parser.add_argument(
        "--allow-incompatible-conditions",
        action="store_true",
        help="Step 3: execute episodes even if action/observation compatibility flag is false.",
    )
    parser.add_argument(
        "--show-gui",
        action="store_true",
        help="Step 3: run CALVIN env with GUI.",
    )
    return parser


def run_step_1(args: argparse.Namespace) -> dict:
    return build_protocol_dataset(
        calvin_root=Path(args.calvin_root),
        output_root=Path(args.output_root),
        seed=args.seed,
        official_total=args.official_total,
        selected_total=args.selected_total,
        track=args.track,
        split=args.split,
        dry_run=args.dry_run,
    )


def run_step_2(args: argparse.Namespace) -> dict:
    return build_scenario_contracts(
        protocol_root=Path(args.protocol_root),
        output_root=Path(args.contracts_output_root),
        force=args.contracts_force,
    )


def run_step_3(args: argparse.Namespace) -> dict:
    python_kwargs = json.loads(args.python_model_kwargs)
    if not isinstance(python_kwargs, dict):
        raise ValueError("--python-model-kwargs must be a JSON object")
    return run_calvin_benchmark_step3(
        calvin_root=Path(args.calvin_root),
        dataset_path=Path(args.dataset_path),
        protocol_root=Path(args.protocol_root),
        contracts_root=Path(args.contracts_root),
        run_root=Path(args.run_root),
        model_id=args.model_id,
        model_family=args.model_family,
        model_backend=args.model_backend,
        model_host=args.model_host,
        model_port=args.model_port,
        model_timeout_sec=args.model_timeout_sec,
        python_model_spec=args.python_model_spec,
        python_model_kwargs=python_kwargs,
        parallel_workers=args.parallel_workers,
        benchmark_size=args.benchmark_size,
        save_frames=args.save_frames,
        max_episodes=args.max_episodes,
        allow_subtask_skip=args.allow_subtask_skip,
        allow_incompatible_conditions=args.allow_incompatible_conditions,
        show_gui=args.show_gui,
    )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.step == 1:
        summary = run_step_1(args)
    elif args.step == 2:
        summary = run_step_2(args)
    elif args.step == 3:
        summary = run_step_3(args)
    else:
        raise RuntimeError(f"Unsupported step: {args.step}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
