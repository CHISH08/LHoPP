import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


STEP_PAYLOAD_FIELDS = [
    "episode_id",
    "step_idx",
    "task_instruction",
    "history_actions",
    "history_events",
    "available_actions_mask",
    "active_modalities",
    "observation_bundle",
    "budget_left_steps",
    "budget_left_time_sec",
]

MODEL_RESPONSE_FIELDS = [
    "status",
    "action_raw",
    "action_exec",
    "model_latency_sec",
    "error_code",
    "error_message",
    "tokens_in",
    "tokens_out",
    "meta",
]

MODEL_ERROR_CODES = [
    "unsupported_model_interface",
    "empty_action",
    "invalid_action_format",
    "action_not_allowed",
    "timeout_model",
    "runtime_exception",
]

EPISODE_LOG_FIELDS = [
    "run_id",
    "track",
    "model_id",
    "family",
    "episode_id",
    "task_id",
    "task_title",
    "stratum",
    "seed",
    "pair_id",
    "condition_id",
    "scenario_level",
    "scenario_variant",
    "scenario_tag",
    "status",
    "max_steps",
    "max_time_sec",
    "steps_total",
    "terminate_reason",
    "decision_time_total_sec",
    "sim_exec_time_total_sec",
    "episode_wallclock_total_sec",
    "started_at_utc",
    "finished_at_utc",
]

STEP_LOG_FIELDS = [
    "run_id",
    "episode_id",
    "step_idx",
    "timestamp_utc",
    "scenario_level",
    "scenario_variant",
    "scenario_tag",
    "active_modalities",
    "mask_size_total",
    "mask_size_allowed",
    "action_raw",
    "action_exec",
    "model_status",
    "model_error_code",
    "model_error_message",
    "sim_success_flag",
    "sim_message",
    "decision_time_step_sec",
    "sim_exec_time_step_sec",
    "episode_wallclock_step_sec",
    "history_size",
    "plan_revision_id",
    "safety_flag",
    "notes",
]

EVENT_LOG_FIELDS = [
    "run_id",
    "episode_id",
    "event_id",
    "step_idx",
    "timestamp_utc",
    "scenario_level",
    "scenario_variant",
    "event_type",
    "event_source",
    "event_payload_json",
    "model_response_before_event",
    "model_response_after_event",
    "resolved_flag",
    "resolve_step_idx",
    "resolve_latency_steps",
    "safety_reaction",
]

CAMERA_MODES = [
    "normal",
    "seg_inst",
    "seg_class",
    "depth",
    "flow",
    "albedo",
    "illumination",
    "surf_normals",
]

# Deterministic budget policy for scenario contracts.
MAX_STEPS_MULTIPLIER = 2
MAX_STEPS_EXTRA = 4
MAX_TIME_PER_STEP_SEC = 6


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "||".join(parts).encode("utf-8")
    digest = hashlib.sha1(raw).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _load_manifests(protocol_root: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    manifest_dir = protocol_root / "manifest"
    task_manifest_path = manifest_dir / "task_manifest.json"
    benchmark_manifest_path = manifest_dir / "benchmark_manifest.json"
    if not task_manifest_path.exists():
        raise FileNotFoundError(f"Missing task manifest: {task_manifest_path}")
    if not benchmark_manifest_path.exists():
        raise FileNotFoundError(f"Missing benchmark manifest: {benchmark_manifest_path}")
    task_manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
    benchmark_manifest = json.loads(benchmark_manifest_path.read_text(encoding="utf-8"))
    return task_manifest, benchmark_manifest


def _build_condition_contract(condition: Dict[str, Any], seed: int) -> Dict[str, Any]:
    level = condition["level"]
    variant = condition["variant"]
    base = {
        "condition_id": condition["condition_id"],
        "condition_id_seeded": f"{condition['condition_id']}.seed{seed}",
        "level": level,
        "variant": variant,
        "sensor_profile": condition["sensor_profile"],
        "perturbation_profile": condition["perturbation_profile"],
        "camera_modes": CAMERA_MODES,
        "observation_contract": {
            "required_step_payload_fields": STEP_PAYLOAD_FIELDS,
            "active_modalities": [],
            "observation_bundle_policy": "",
        },
        "action_contract": {
            "mask_enforced": True,
            "validation_rules": [
                "virtualhome_command_format",
                "in_available_actions_mask",
                "respect_condition_constraints",
                "unity_canonicalization_passed",
            ],
            "distractor_policy": {"enabled": False, "mode": "none"},
        },
        "perturbation_contract": {
            "event_schedule_mode": "deterministic_seeded",
            "events": [],
        },
    }

    if level == "L1":
        base["observation_contract"]["active_modalities"] = ["graph", "camera", "history", "action_mask"]
        base["observation_contract"]["observation_bundle_policy"] = "full_nominal"
    elif level == "L2":
        base["observation_contract"]["active_modalities"] = ["state", "history", "action_mask"]
        base["observation_contract"]["observation_bundle_policy"] = "sensor_blackout"
        base["perturbation_contract"]["events"] = [
            {
                "event_type": "sensor_blackout",
                "event_source": "scenario",
                "payload": {"target": "all_sensors", "mode": "persistent"},
            }
        ]
    elif level == "L3":
        base["observation_contract"]["active_modalities"] = ["graph", "camera", "history", "action_mask"]
        base["observation_contract"]["observation_bundle_policy"] = "action_constrained"
        base["action_contract"]["distractor_policy"] = {
            "enabled": True,
            "mode": "allowed_but_non_progressive",
            "injection_rule": "deterministic_seeded_steps",
        }
        base["perturbation_contract"]["events"] = [
            {
                "event_type": "action_mask_change",
                "event_source": "scenario",
                "payload": {"mask_mode": "strict", "distractors": "enabled"},
            }
        ]
    elif level == "L4":
        base["observation_contract"]["active_modalities"] = ["graph", "camera", "history", "action_mask"]
        base["observation_contract"]["observation_bundle_policy"] = "sensor_stress"
        if variant == "l4_cam_subset":
            base["perturbation_contract"]["events"] = [
                {
                    "event_type": "sensor_blackout",
                    "event_source": "scenario",
                    "payload": {"target": "camera_subset", "selection": "deterministic_seeded"},
                }
            ]
        elif variant == "l4_cam_blackout":
            base["perturbation_contract"]["events"] = [
                {
                    "event_type": "sensor_blackout",
                    "event_source": "scenario",
                    "payload": {"target": "all_cameras", "mode": "persistent"},
                }
            ]
        elif variant == "l4_sensor_noise":
            base["perturbation_contract"]["events"] = [
                {
                    "event_type": "sensor_noise",
                    "event_source": "scenario",
                    "payload": {
                        "target": "camera",
                        "noise_profile": "deterministic_seeded",
                        "mode": "persistent",
                    },
                }
            ]
        elif variant == "l4_mixed_ablation_noise":
            base["perturbation_contract"]["events"] = [
                {
                    "event_type": "sensor_blackout",
                    "event_source": "scenario",
                    "payload": {"target": "camera_subset", "selection": "deterministic_seeded"},
                },
                {
                    "event_type": "sensor_noise",
                    "event_source": "scenario",
                    "payload": {
                        "target": "camera",
                        "noise_profile": "deterministic_seeded",
                        "mode": "persistent",
                    },
                },
            ]
        else:
            raise RuntimeError(f"Unknown L4 variant: {variant}")
    elif level == "L5":
        base["observation_contract"]["active_modalities"] = ["graph", "camera", "history", "action_mask"]
        base["observation_contract"]["observation_bundle_policy"] = "contradictory_random_action_injection"
        base["perturbation_contract"]["events"] = [
            {
                "event_type": "injected_random_action",
                "event_source": "scenario",
                "payload": {"mode": "contradict_model_action", "schedule": "deterministic_seeded_steps"},
            }
        ]
    else:
        raise RuntimeError(f"Unknown condition level: {level}")

    return base


def _l5_injection_steps(max_steps: int) -> List[int]:
    cands = {
        max(2, int(round(max_steps * 0.25))),
        max(3, int(round(max_steps * 0.50))),
        max(4, int(round(max_steps * 0.75))),
    }
    return sorted([s for s in cands if s <= max_steps])


def _episode_event_schedule(episode_contract: Dict[str, Any], condition_contract: Dict[str, Any]) -> List[Dict[str, Any]]:
    max_steps = episode_contract["max_steps"]
    level = condition_contract["level"]
    variant = condition_contract["variant"]
    events: List[Dict[str, Any]] = []

    def add_event(event_type: str, source: str, start_step: int, end_step: int, payload: Dict[str, Any]) -> None:
        idx = len(events) + 1
        events.append(
            {
                "event_id": f"evt_{idx:02d}",
                "event_type": event_type,
                "event_source": source,
                "start_step": start_step,
                "end_step": end_step,
                "payload": payload,
            }
        )

    if level == "L1":
        return events

    if level == "L2":
        add_event("sensor_blackout", "scenario", 1, max_steps, {"target": "all_sensors"})
        return events

    if level == "L3":
        add_event("action_mask_change", "scenario", 1, max_steps, {"mask_mode": "strict"})
        mid = max(2, max_steps // 2)
        add_event(
            "action_mask_change",
            "scenario",
            mid,
            mid,
            {"distractor_actions": "enabled", "injection_mode": "deterministic"},
        )
        return events

    if level == "L4":
        if variant == "l4_cam_subset":
            add_event(
                "sensor_blackout",
                "scenario",
                1,
                max_steps,
                {"target": "camera_subset", "selection": "deterministic_seeded"},
            )
        elif variant == "l4_cam_blackout":
            add_event("sensor_blackout", "scenario", 1, max_steps, {"target": "all_cameras"})
        elif variant == "l4_sensor_noise":
            add_event(
                "sensor_noise",
                "scenario",
                1,
                max_steps,
                {"target": "camera", "noise_profile": "deterministic_seeded"},
            )
        elif variant == "l4_mixed_ablation_noise":
            add_event(
                "sensor_blackout",
                "scenario",
                1,
                max_steps,
                {"target": "camera_subset", "selection": "deterministic_seeded"},
            )
            add_event(
                "sensor_noise",
                "scenario",
                1,
                max_steps,
                {"target": "camera", "noise_profile": "deterministic_seeded"},
            )
        else:
            raise RuntimeError(f"Unknown L4 variant in schedule: {variant}")
        return events

    if level == "L5":
        for step_idx in _l5_injection_steps(max_steps):
            add_event(
                "injected_random_action",
                "scenario",
                step_idx,
                step_idx,
                {"mode": "contradict_model_action", "deterministic": True},
            )
        return events

    raise RuntimeError(f"Unknown level in schedule: {level}")


def build_scenario_contracts(
    protocol_root: Path,
    output_root: Path | None = None,
    force: bool = False,
) -> Dict[str, Any]:
    protocol_root = protocol_root.resolve()
    if output_root is None:
        output_root = protocol_root / "contracts"
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    scenario_manifest_path = output_root / "scenario_contract_manifest.json"
    if scenario_manifest_path.exists() and not force:
        data = json.loads(scenario_manifest_path.read_text(encoding="utf-8"))
        return {
            "status": "skipped_existing",
            "scenario_contract_manifest": str(scenario_manifest_path),
            "run_signature": data.get("run_signature"),
            "episodes_total": data.get("episodes_total"),
            "steps_total": data.get("steps_total"),
            "events_total": data.get("events_total"),
        }

    task_manifest, benchmark_manifest = _load_manifests(protocol_root=protocol_root)
    tasks = list(task_manifest.get("tasks", []))
    conditions = list(benchmark_manifest.get("conditions", []))
    selection_seed = int(benchmark_manifest.get("selection_seed", task_manifest.get("selection_seed", 42)))
    track = str(benchmark_manifest.get("track", "unified_ranking"))

    if not tasks:
        raise RuntimeError("task_manifest has no tasks")
    if not conditions:
        raise RuntimeError("benchmark_manifest has no conditions")

    condition_contracts = [_build_condition_contract(c, selection_seed) for c in conditions]
    condition_by_id = {c["condition_id"]: c for c in condition_contracts}

    episodes_rows: List[Dict[str, Any]] = []
    steps_rows: List[Dict[str, Any]] = []
    events_rows: List[Dict[str, Any]] = []

    episode_idx = 0
    for task in tasks:
        task_id = str(task["task_id"])
        stratum = str(task["stratum"])
        actions_count = int(task["actions_count"])
        for condition in conditions:
            episode_idx += 1
            condition_id = str(condition["condition_id"])
            cc = condition_by_id[condition_id]
            level = str(condition["level"])
            variant = str(condition["variant"])

            episode_id = _stable_id("ep", track, condition_id, task_id, str(selection_seed))
            pair_id = f"pair::{track}::{task_id}::{selection_seed}"
            scenario_tag = f"{level}:{variant}:{stratum}"

            max_steps = max(actions_count * MAX_STEPS_MULTIPLIER, actions_count + MAX_STEPS_EXTRA)
            max_time_sec = max_steps * MAX_TIME_PER_STEP_SEC

            episode_contract = {
                "episode_id": episode_id,
                "pair_id": pair_id,
                "task_id": task_id,
                "relative_path": task["relative_path"],
                "scene": task["scene"],
                "stratum": stratum,
                "reference_actions_count": actions_count,
                "condition_id": condition_id,
                "condition_id_seeded": cc["condition_id_seeded"],
                "scenario_level": level,
                "scenario_variant": variant,
                "scenario_tag": scenario_tag,
                "seed": selection_seed,
                "track": track,
                "max_steps": max_steps,
                "max_time_sec": max_time_sec,
                "active_modalities": cc["observation_contract"]["active_modalities"],
                "required_step_payload_fields": STEP_PAYLOAD_FIELDS,
                "required_model_response_fields": MODEL_RESPONSE_FIELDS,
                "allowed_model_error_codes": MODEL_ERROR_CODES,
                "required_episode_log_fields": EPISODE_LOG_FIELDS,
                "required_step_log_fields": STEP_LOG_FIELDS,
                "required_event_log_fields": EVENT_LOG_FIELDS,
            }
            episodes_rows.append(
                {
                    "episode_id": episode_id,
                    "pair_id": pair_id,
                    "task_id": task_id,
                    "stratum": stratum,
                    "condition_id": condition_id,
                    "condition_id_seeded": cc["condition_id_seeded"],
                    "scenario_level": level,
                    "scenario_variant": variant,
                    "scenario_tag": scenario_tag,
                    "seed": selection_seed,
                    "track": track,
                    "reference_actions_count": actions_count,
                    "max_steps": max_steps,
                    "max_time_sec": max_time_sec,
                    "active_modalities_json": _json_cell(cc["observation_contract"]["active_modalities"]),
                    "observation_bundle_policy": cc["observation_contract"]["observation_bundle_policy"],
                }
            )

            event_schedule = _episode_event_schedule(episode_contract=episode_contract, condition_contract=cc)
            events_by_step: Dict[int, List[str]] = {}
            for event in event_schedule:
                events_rows.append(
                    {
                        "episode_id": episode_id,
                        "condition_id": condition_id,
                        "scenario_level": level,
                        "scenario_variant": variant,
                        "event_id": event["event_id"],
                        "event_type": event["event_type"],
                        "event_source": event["event_source"],
                        "start_step": event["start_step"],
                        "end_step": event["end_step"],
                        "event_payload_json": _json_cell(event["payload"]),
                    }
                )
                for step_idx in range(int(event["start_step"]), int(event["end_step"]) + 1):
                    events_by_step.setdefault(step_idx, []).append(event["event_id"])

            for step_idx in range(1, max_steps + 1):
                steps_rows.append(
                    {
                        "episode_id": episode_id,
                        "condition_id": condition_id,
                        "scenario_level": level,
                        "scenario_variant": variant,
                        "step_idx": step_idx,
                        "active_modalities_json": _json_cell(cc["observation_contract"]["active_modalities"]),
                        "required_step_payload_fields_json": _json_cell(STEP_PAYLOAD_FIELDS),
                        "required_model_response_fields_json": _json_cell(MODEL_RESPONSE_FIELDS),
                        "required_step_log_fields_json": _json_cell(STEP_LOG_FIELDS),
                        "active_event_ids_json": _json_cell(events_by_step.get(step_idx, [])),
                        "action_mask_enforced": str(bool(cc["action_contract"]["mask_enforced"])).lower(),
                        "observation_bundle_policy": cc["observation_contract"]["observation_bundle_policy"],
                    }
                )

    conditions_path = output_root / "conditions_contracts.json"
    schema_refs_path = output_root / "schema_refs.json"
    episodes_path = output_root / "episodes_contracts.csv"
    steps_path = output_root / "steps_contracts.csv"
    events_path = output_root / "events_schedule.csv"

    conditions_path.write_text(json.dumps(condition_contracts, ensure_ascii=False, indent=2), encoding="utf-8")
    schema_refs_path.write_text(
        json.dumps(
            {
                "step_payload_fields": STEP_PAYLOAD_FIELDS,
                "model_response_fields": MODEL_RESPONSE_FIELDS,
                "model_error_codes": MODEL_ERROR_CODES,
                "episode_log_fields": EPISODE_LOG_FIELDS,
                "step_log_fields": STEP_LOG_FIELDS,
                "event_log_fields": EVENT_LOG_FIELDS,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _write_csv(
        episodes_path,
        fieldnames=[
            "episode_id",
            "pair_id",
            "task_id",
            "stratum",
            "condition_id",
            "condition_id_seeded",
            "scenario_level",
            "scenario_variant",
            "scenario_tag",
            "seed",
            "track",
            "reference_actions_count",
            "max_steps",
            "max_time_sec",
            "active_modalities_json",
            "observation_bundle_policy",
        ],
        rows=episodes_rows,
    )
    _write_csv(
        steps_path,
        fieldnames=[
            "episode_id",
            "condition_id",
            "scenario_level",
            "scenario_variant",
            "step_idx",
            "active_modalities_json",
            "required_step_payload_fields_json",
            "required_model_response_fields_json",
            "required_step_log_fields_json",
            "active_event_ids_json",
            "action_mask_enforced",
            "observation_bundle_policy",
        ],
        rows=steps_rows,
    )
    _write_csv(
        events_path,
        fieldnames=[
            "episode_id",
            "condition_id",
            "scenario_level",
            "scenario_variant",
            "event_id",
            "event_type",
            "event_source",
            "start_step",
            "end_step",
            "event_payload_json",
        ],
        rows=events_rows,
    )

    file_hashes = {
        "conditions_contracts.json": _sha256_file(conditions_path),
        "schema_refs.json": _sha256_file(schema_refs_path),
        "episodes_contracts.csv": _sha256_file(episodes_path),
        "steps_contracts.csv": _sha256_file(steps_path),
        "events_schedule.csv": _sha256_file(events_path),
    }

    run_signature = hashlib.sha256(
        json.dumps(
            {
                "task_manifest_hash": _sha256_file(protocol_root / "manifest" / "task_manifest.json"),
                "benchmark_manifest_hash": _sha256_file(protocol_root / "manifest" / "benchmark_manifest.json"),
                "selection_seed": selection_seed,
                "track": track,
                "budget_policy": {
                    "max_steps_multiplier": MAX_STEPS_MULTIPLIER,
                    "max_steps_extra": MAX_STEPS_EXTRA,
                    "max_time_per_step_sec": MAX_TIME_PER_STEP_SEC,
                },
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    scenario_manifest = {
        "schema_version": "v1",
        "generated_from": {
            "task_manifest": str((protocol_root / "manifest" / "task_manifest.json").resolve()),
            "benchmark_manifest": str((protocol_root / "manifest" / "benchmark_manifest.json").resolve()),
            "task_manifest_sha256": _sha256_file(protocol_root / "manifest" / "task_manifest.json"),
            "benchmark_manifest_sha256": _sha256_file(protocol_root / "manifest" / "benchmark_manifest.json"),
        },
        "run_signature": run_signature,
        "selection_seed": selection_seed,
        "track": track,
        "conditions_total": len(condition_contracts),
        "episodes_total": len(episodes_rows),
        "steps_total": len(steps_rows),
        "events_total": len(events_rows),
        "budget_policy": {
            "max_steps_formula": "max(actions_count*2, actions_count+4)",
            "max_time_sec_formula": "max_steps*6",
        },
        "assumptions": [
            "Scenario parameters are generated from docs contracts and benchmark manifests.",
            "Noise/ablation payloads are deterministic-seeded placeholders for runtime execution.",
            "Success oracle is not set in this step; this step defines machine-readable scenario contracts.",
        ],
        "output_files": {
            "conditions_contracts": str(conditions_path),
            "schema_refs": str(schema_refs_path),
            "episodes_contracts_csv": str(episodes_path),
            "steps_contracts_csv": str(steps_path),
            "events_schedule_csv": str(events_path),
        },
        "output_file_hashes_sha256": file_hashes,
    }
    scenario_manifest_path.write_text(
        json.dumps(scenario_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "status": "generated",
        "scenario_contract_manifest": str(scenario_manifest_path),
        "run_signature": run_signature,
        "conditions_total": len(condition_contracts),
        "episodes_total": len(episodes_rows),
        "steps_total": len(steps_rows),
        "events_total": len(events_rows),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build machine-readable scenario contracts from protocol manifests."
    )
    parser.add_argument(
        "--protocol-root",
        default="estimate_scripts/protocol_bundle",
        help="Path to protocol bundle folder produced by step 1.",
    )
    parser.add_argument(
        "--output-root",
        default="estimate_scripts/protocol_bundle/contracts",
        help="Output folder for generated scenario contracts.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing scenario contract files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    summary = build_scenario_contracts(
        protocol_root=Path(args.protocol_root),
        output_root=Path(args.output_root),
        force=args.force,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

