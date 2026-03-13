import argparse
import csv
import hashlib
import json
import random
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Tuple


ACTION_PATTERN = re.compile(r"^(<char\d+\s*\[|\[)")


@dataclass(frozen=True)
class TaskItem:
    task_id: str
    relative_path: str
    actions_count: int
    stratum: str
    scene: str


def _count_actions(path: Path) -> int:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    cleaned = [x.strip() for x in lines if x.strip()]
    return sum(1 for line in cleaned if ACTION_PATTERN.match(line))


def _classify_stratum(actions_count: int) -> str:
    if actions_count <= 5:
        return "easy"
    if actions_count <= 12:
        return "medium"
    return "hard"


def _deterministic_shuffle(items: List[TaskItem], seed: int, salt: str) -> List[TaskItem]:
    token = f"{seed}:{salt}".encode("utf-8")
    stratum_seed = int(hashlib.sha256(token).hexdigest()[:16], 16)
    rng = random.Random(stratum_seed)
    out = list(items)
    rng.shuffle(out)
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_conditions(track: str) -> List[Dict[str, str]]:
    return [
        {
            "level": "L1",
            "variant": "ideal",
            "sensor_profile": "full",
            "perturbation_profile": "nominal",
            "condition_id": f"{track}.L1.full.nominal.base",
        },
        {
            "level": "L2",
            "variant": "no_sensor",
            "sensor_profile": "none",
            "perturbation_profile": "no_sensor",
            "condition_id": f"{track}.L2.none.no_sensor.base",
        },
        {
            "level": "L3",
            "variant": "action_constrained",
            "sensor_profile": "masked",
            "perturbation_profile": "action_constrained",
            "condition_id": f"{track}.L3.masked.action_constrained.base",
        },
        {
            "level": "L4",
            "variant": "l4_cam_subset",
            "sensor_profile": "camera",
            "perturbation_profile": "sensor_stress",
            "condition_id": f"{track}.L4.camera.sensor_stress.l4_cam_subset",
        },
        {
            "level": "L4",
            "variant": "l4_cam_blackout",
            "sensor_profile": "camera",
            "perturbation_profile": "sensor_stress",
            "condition_id": f"{track}.L4.camera.sensor_stress.l4_cam_blackout",
        },
        {
            "level": "L4",
            "variant": "l4_sensor_noise",
            "sensor_profile": "camera",
            "perturbation_profile": "sensor_stress",
            "condition_id": f"{track}.L4.camera.sensor_stress.l4_sensor_noise",
        },
        {
            "level": "L4",
            "variant": "l4_mixed_ablation_noise",
            "sensor_profile": "camera",
            "perturbation_profile": "sensor_stress",
            "condition_id": f"{track}.L4.camera.sensor_stress.l4_mixed_ablation_noise",
        },
        {
            "level": "L5",
            "variant": "contradictory_random_actions",
            "sensor_profile": "mixed",
            "perturbation_profile": "random_contradict",
            "condition_id": f"{track}.L5.mixed.random_contradict.base",
        },
    ]


def build_protocol_dataset(
    dataset_root: Path,
    output_root: Path,
    seed: int = 42,
    per_stratum: int = 30,
    track: str = "unified_ranking",
    dry_run: bool = False,
) -> Dict[str, object]:
    dataset_root = dataset_root.resolve()
    output_root = output_root.resolve()

    if not dataset_root.exists():
        raise FileNotFoundError(f"Dataset root not found: {dataset_root}")

    all_files = sorted(dataset_root.rglob("*.txt"))
    if not all_files:
        raise RuntimeError(f"No .txt tasks found in: {dataset_root}")

    tasks: List[TaskItem] = []
    for fp in all_files:
        rel = fp.relative_to(dataset_root).as_posix()
        actions_count = _count_actions(fp)
        if actions_count <= 0:
            continue
        scene = rel.split("/")[0] if "/" in rel else "unknown_scene"
        tasks.append(
            TaskItem(
                task_id=rel,
                relative_path=rel,
                actions_count=actions_count,
                stratum=_classify_stratum(actions_count),
                scene=scene,
            )
        )

    pools: Dict[str, List[TaskItem]] = {
        "easy": [x for x in tasks if x.stratum == "easy"],
        "medium": [x for x in tasks if x.stratum == "medium"],
        "hard": [x for x in tasks if x.stratum == "hard"],
    }

    selected: List[TaskItem] = []
    for stratum in ("easy", "medium", "hard"):
        if len(pools[stratum]) < per_stratum:
            raise RuntimeError(
                f"Not enough tasks in stratum={stratum}: have={len(pools[stratum])}, need={per_stratum}"
            )
        shuffled = _deterministic_shuffle(pools[stratum], seed=seed, salt=stratum)
        selected.extend(shuffled[:per_stratum])

    selected = sorted(selected, key=lambda x: (x.stratum, x.relative_path))
    conditions = _default_conditions(track=track)
    expected_episodes_per_model = len(selected) * len(conditions)

    task_manifest = {
        "schema_version": "v1",
        "dataset_root": str(dataset_root),
        "selection_seed": seed,
        "per_stratum": per_stratum,
        "selected_total": len(selected),
        "strata": {
            "easy": sum(1 for x in selected if x.stratum == "easy"),
            "medium": sum(1 for x in selected if x.stratum == "medium"),
            "hard": sum(1 for x in selected if x.stratum == "hard"),
        },
        "tasks": [asdict(x) for x in selected],
    }

    benchmark_manifest = {
        "schema_version": "v1",
        "protocol_version": "unity_only_stage1",
        "track": track,
        "selection_seed": seed,
        "per_stratum": per_stratum,
        "conditions": conditions,
        "selected_tasks_total": len(selected),
        "episodes_per_model": expected_episodes_per_model,
    }

    if dry_run:
        return {
            "dataset_root": str(dataset_root),
            "output_root": str(output_root),
            "selected_total": len(selected),
            "episodes_per_model": expected_episodes_per_model,
            "strata_counts": task_manifest["strata"],
            "conditions_count": len(conditions),
            "dry_run": True,
        }

    manifests_dir = output_root / "manifest"
    tasks_dir = output_root / "data" / "tasks"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # Copy selected task files into a single bundle folder.
    for item in selected:
        src = dataset_root / item.relative_path
        dst = tasks_dir / item.relative_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    selected_csv = output_root / "data" / "selected_tasks.csv"
    selected_csv.parent.mkdir(parents=True, exist_ok=True)
    with selected_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["task_id", "relative_path", "actions_count", "stratum", "scene"],
        )
        writer.writeheader()
        for item in selected:
            writer.writerow(asdict(item))

    task_manifest_path = manifests_dir / "task_manifest.json"
    benchmark_manifest_path = manifests_dir / "benchmark_manifest.json"
    task_manifest_path.write_text(json.dumps(task_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    benchmark_manifest_path.write_text(json.dumps(benchmark_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    (manifests_dir / "task_manifest.sha256").write_text(_sha256_file(task_manifest_path), encoding="utf-8")
    (manifests_dir / "benchmark_manifest.sha256").write_text(
        _sha256_file(benchmark_manifest_path), encoding="utf-8"
    )

    return {
        "dataset_root": str(dataset_root),
        "output_root": str(output_root),
        "selected_total": len(selected),
        "episodes_per_model": expected_episodes_per_model,
        "strata_counts": task_manifest["strata"],
        "conditions_count": len(conditions),
        "dry_run": False,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build deterministic protocol dataset bundle.")
    parser.add_argument(
        "--dataset-root",
        default="virtualhome/virtualhome/dataset/programs_processed_precond_nograb_morepreconds/executable_programs",
        help="Path to executable_programs root.",
    )
    parser.add_argument(
        "--output-root",
        default="estimate_scripts/protocol_bundle",
        help="Where to write the bundled dataset/manifests.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--per-stratum", type=int, default=30)
    parser.add_argument("--track", default="unified_ranking")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    summary = build_protocol_dataset(
        dataset_root=Path(args.dataset_root),
        output_root=Path(args.output_root),
        seed=args.seed,
        per_stratum=args.per_stratum,
        track=args.track,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

