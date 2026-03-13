import argparse
import ast
import contextlib
import csv
import hashlib
import importlib.util
import json
import sys
import types
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np


@dataclass(frozen=True)
class ScenarioItem:
    sequence_id: str
    official_sequence_index: int
    initial_state_id: str
    initial_state: Dict[str, object]
    subtask_list: List[str]
    instruction_texts: List[str]
    task_categories: List[int]
    sequence_length: int


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _state_id(state: Dict[str, object]) -> str:
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return f"state_{_sha256_text(canonical)[:16]}"


def _load_validation_annotations(path: Path) -> Dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Validation annotations file not found: {path}")

    annotations: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue

        task_name, raw_values = line.split(":", 1)
        task_name = task_name.strip()
        raw_values = raw_values.strip()
        if not task_name or not raw_values:
            continue

        try:
            parsed = ast.literal_eval(raw_values)
        except (ValueError, SyntaxError):
            continue

        if isinstance(parsed, list) and parsed:
            annotations[task_name] = str(parsed[0])

    if not annotations:
        raise RuntimeError(f"Could not parse any task annotations from: {path}")
    return annotations


def _load_multistep_module(multistep_path: Path):
    if not multistep_path.exists():
        raise FileNotFoundError(f"multistep_sequences.py not found: {multistep_path}")

    @contextlib.contextmanager
    def temp_seed(seed: int):
        state = np.random.get_state()
        np.random.seed(seed)
        try:
            yield
        finally:
            np.random.set_state(state)

    # Avoid importing full calvin_agent.evaluation.utils (torch/cv2 heavy dependencies).
    fake_utils = types.ModuleType("calvin_agent.evaluation.utils")
    fake_utils.temp_seed = temp_seed

    fake_eval = types.ModuleType("calvin_agent.evaluation")
    fake_eval.utils = fake_utils

    fake_root = types.ModuleType("calvin_agent")
    fake_root.evaluation = fake_eval

    previous_modules = {
        "calvin_agent": sys.modules.get("calvin_agent"),
        "calvin_agent.evaluation": sys.modules.get("calvin_agent.evaluation"),
        "calvin_agent.evaluation.utils": sys.modules.get("calvin_agent.evaluation.utils"),
    }
    sys.modules["calvin_agent"] = fake_root
    sys.modules["calvin_agent.evaluation"] = fake_eval
    sys.modules["calvin_agent.evaluation.utils"] = fake_utils

    try:
        module_name = "calvin_stage1_multistep_sequences"
        spec = importlib.util.spec_from_file_location(module_name, multistep_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Failed to load module spec from: {multistep_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in previous_modules.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def _generate_official_sequences(multistep_module, num_sequences: int) -> List[Tuple[Dict[str, object], Tuple[str, ...]]]:
    if num_sequences <= 0:
        raise ValueError(f"num_sequences must be positive, got {num_sequences}")

    possible_conditions = {
        "led": [0, 1],
        "lightbulb": [0, 1],
        "slider": ["right", "left"],
        "drawer": ["closed", "open"],
        "red_block": ["table", "slider_right", "slider_left"],
        "blue_block": ["table", "slider_right", "slider_left"],
        "pink_block": ["table", "slider_right", "slider_left"],
        "grasped": [0],
    }

    valid_combination = (
        lambda values: values.count("table") in [1, 2]
        and values.count("slider_right") < 2
        and values.count("slider_left") < 2
    )
    combinations = filter(valid_combination, product(*possible_conditions.values()))
    initial_states = [dict(zip(possible_conditions.keys(), values)) for values in combinations]
    sequences_per_state = list(map(len, np.array_split(range(num_sequences), len(initial_states))))

    with multistep_module.temp_seed(0):
        generated = []
        for index, (state, count) in enumerate(zip(initial_states, sequences_per_state)):
            if count == 0:
                continue
            generated.append(multistep_module.get_sequences_for_state2((state, count, index)))

        flat_sequences = multistep_module.flatten(generated)
        scenarios = list(zip(np.repeat(initial_states, sequences_per_state), flat_sequences))
        np.random.shuffle(scenarios)

    return scenarios


def _deterministic_select(items: Iterable[ScenarioItem], seed: int, total: int) -> List[ScenarioItem]:
    ranked = []
    for item in items:
        token = f"{seed}:{item.official_sequence_index}"
        rank = _sha256_text(token)
        ranked.append((rank, item))
    ranked.sort(key=lambda pair: pair[0])
    selected = [pair[1] for pair in ranked[:total]]
    return sorted(selected, key=lambda item: item.official_sequence_index)


def _default_action_levels() -> List[Dict[str, str]]:
    return [
        {"action_level_id": "L1", "action_repr": "textual_subtasks"},
        {"action_level_id": "L2", "action_repr": "absolute_cartesian_tcp"},
        {"action_level_id": "L3", "action_repr": "relative_cartesian_7d"},
        {"action_level_id": "L4", "action_repr": "joint_space"},
    ]


def _default_scenario_profiles() -> List[str]:
    return [
        "ideal",
        "sensor_dropout",
        "sensor_noise",
        "safety_blackout_safe_abstain",
        "safety_blackout_best_effort",
        "recovery_wrong_action",
        "mixed_stress",
    ]


def _default_observation_profiles() -> List[str]:
    return [
        "lang_rgb_static_rel_act",
        "lang_rgb_static_gripper_rel_act",
        "lang_rgbd_static_gripper_rel_act",
        "lang_rgbd_both_rel_act",
        "lang_rgb_static_tactile_rel_act",
        "lang_rgb_static_robot_scene_abs_act",
    ]


def build_protocol_dataset(
    calvin_root: Path,
    output_root: Path,
    seed: int = 42,
    official_total: int = 1000,
    selected_total: int = 1000,
    track: str = "unified_ranking",
    split: str = "validation",
    dry_run: bool = False,
) -> Dict[str, object]:
    calvin_root = calvin_root.resolve()
    output_root = output_root.resolve()
    if not calvin_root.exists():
        raise FileNotFoundError(f"CALVIN root not found: {calvin_root}")
    if selected_total <= 0:
        raise ValueError(f"selected_total must be positive, got {selected_total}")
    if official_total <= 0:
        raise ValueError(f"official_total must be positive, got {official_total}")
    if selected_total > official_total:
        raise ValueError(
            f"selected_total cannot be larger than official_total: {selected_total} > {official_total}"
        )

    multistep_path = calvin_root / "calvin_models" / "calvin_agent" / "evaluation" / "multistep_sequences.py"
    annotations_path = (
        calvin_root / "calvin_models" / "conf" / "annotations" / "new_playtable_validation.yaml"
    )

    multistep_module = _load_multistep_module(multistep_path)
    annotations = _load_validation_annotations(annotations_path)
    official_scenarios = _generate_official_sequences(multistep_module, num_sequences=official_total)

    scenario_items: List[ScenarioItem] = []
    for index, (initial_state, sequence) in enumerate(official_scenarios):
        subtask_list = list(sequence)
        instructions = [annotations.get(task_name, task_name) for task_name in subtask_list]
        categories = [int(multistep_module.task_categories[task_name]) for task_name in subtask_list]
        scenario_items.append(
            ScenarioItem(
                sequence_id=f"calvin_seq_{index:04d}",
                official_sequence_index=index,
                initial_state_id=_state_id(initial_state),
                initial_state={k: initial_state[k] for k in sorted(initial_state.keys())},
                subtask_list=subtask_list,
                instruction_texts=instructions,
                task_categories=categories,
                sequence_length=len(subtask_list),
            )
        )

    selected_items = _deterministic_select(scenario_items, seed=seed, total=selected_total)

    action_levels = _default_action_levels()
    scenario_profiles = _default_scenario_profiles()
    observation_profiles = _default_observation_profiles()
    matrix_cells = len(action_levels) * len(scenario_profiles)
    created_at_utc = datetime.now(timezone.utc).isoformat()

    task_manifest = {
        "schema_version": "v1",
        "protocol_version": "calvin_stage1",
        "created_at_utc": created_at_utc,
        "calvin_root": str(calvin_root),
        "split": split,
        "selection_seed": seed,
        "official_total": official_total,
        "selected_total": len(selected_items),
        "source_files": {
            "multistep_sequences": str(multistep_path),
            "validation_annotations": str(annotations_path),
        },
        "reference": {
            "seq_len": 5,
            "num_sequences": official_total,
            "ep_len": 360,
        },
        "tasks": [asdict(item) for item in selected_items],
    }

    benchmark_manifest = {
        "schema_version": "v1",
        "protocol_version": "calvin_stage1",
        "created_at_utc": created_at_utc,
        "track": track,
        "selection_seed": seed,
        "split": split,
        "selected_sequences_total": len(selected_items),
        "action_levels": action_levels,
        "scenario_profiles": scenario_profiles,
        "observation_profiles": observation_profiles,
        "matrix": {
            "action_levels_count": len(action_levels),
            "scenario_profiles_count": len(scenario_profiles),
            "matrix_cells": matrix_cells,
            "reference_episodes_per_model": len(selected_items) * matrix_cells,
        },
        "rules": {
            "single_sensor_comparison": (
                "For sensor comparison, observation_profile_id pairs must differ by exactly one channel."
            )
        },
    }

    summary = {
        "calvin_root": str(calvin_root),
        "output_root": str(output_root),
        "official_total": official_total,
        "selected_total": len(selected_items),
        "matrix_cells": matrix_cells,
        "reference_episodes_per_model": len(selected_items) * matrix_cells,
        "dry_run": dry_run,
        "first_selected_sequence_ids": [item.sequence_id for item in selected_items[:5]],
    }
    if dry_run:
        return summary

    manifest_dir = output_root / "manifest"
    data_dir = output_root / "data"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    csv_path = data_dir / "selected_sequences.csv"
    jsonl_path = data_dir / "selected_sequences.jsonl"
    csv_fieldnames = [
        "sequence_id",
        "official_sequence_index",
        "initial_state_id",
        "initial_state_json",
        "sequence_length",
        "subtask_1",
        "subtask_2",
        "subtask_3",
        "subtask_4",
        "subtask_5",
        "instruction_1",
        "instruction_2",
        "instruction_3",
        "instruction_4",
        "instruction_5",
        "task_category_1",
        "task_category_2",
        "task_category_3",
        "task_category_4",
        "task_category_5",
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fieldnames)
        writer.writeheader()
        for item in selected_items:
            row = {
                "sequence_id": item.sequence_id,
                "official_sequence_index": item.official_sequence_index,
                "initial_state_id": item.initial_state_id,
                "initial_state_json": json.dumps(item.initial_state, ensure_ascii=False, sort_keys=True),
                "sequence_length": item.sequence_length,
            }
            for index, subtask in enumerate(item.subtask_list, start=1):
                row[f"subtask_{index}"] = subtask
            for index, instruction in enumerate(item.instruction_texts, start=1):
                row[f"instruction_{index}"] = instruction
            for index, category in enumerate(item.task_categories, start=1):
                row[f"task_category_{index}"] = category
            writer.writerow(row)

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for item in selected_items:
            handle.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")

    task_manifest_path = manifest_dir / "task_manifest.json"
    benchmark_manifest_path = manifest_dir / "benchmark_manifest.json"
    task_manifest_path.write_text(json.dumps(task_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    benchmark_manifest_path.write_text(
        json.dumps(benchmark_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (manifest_dir / "task_manifest.sha256").write_text(_sha256_file(task_manifest_path), encoding="utf-8")
    (manifest_dir / "benchmark_manifest.sha256").write_text(
        _sha256_file(benchmark_manifest_path), encoding="utf-8"
    )

    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build deterministic CALVIN protocol dataset bundle (step 1)."
    )
    parser.add_argument("--calvin-root", default="calvin_bench/calvin")
    parser.add_argument("--output-root", default="calvin_bench/estimate_scripts/protocol_bundle")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--official-total", type=int, default=1000)
    parser.add_argument("--selected-total", type=int, default=1000)
    parser.add_argument("--track", default="unified_ranking")
    parser.add_argument("--split", default="validation")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    summary = build_protocol_dataset(
        calvin_root=Path(args.calvin_root),
        output_root=Path(args.output_root),
        seed=args.seed,
        official_total=args.official_total,
        selected_total=args.selected_total,
        track=args.track,
        split=args.split,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

