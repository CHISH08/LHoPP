import argparse
import ast
import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


STEP_PAYLOAD_FIELDS = [
    "run_id",
    "episode_id",
    "step_idx",
    "subtask_idx",
    "current_instruction_text",
    "oracle_target_subtask",
    "action_level_id",
    "scenario_profile_id",
    "observation_profile_id",
    "active_modalities",
    "active_sensor_mask",
    "dropped_modalities",
    "noise_profile",
    "safety_contract",
    "event_context",
    "observation_bundle",
    "history_actions",
    "history_events",
    "budget_left_subtask_steps",
    "budget_left_episode_steps",
    "budget_left_time_sec",
    "active_constraints",
]

MODEL_RESPONSE_FIELDS = [
    "status",
    "action_raw",
    "action_exec",
    "action_level_id",
    "model_latency_sec",
    "error_code",
    "error_message",
    "safety_intent",
    "fallback_mode",
    "replan_requested",
    "rollback_requested",
    "tokens_in",
    "tokens_out",
    "meta",
]

MODEL_ERROR_CODES = [
    "unsupported_model_interface",
    "empty_action",
    "invalid_action_format",
    "action_level_mismatch",
    "scenario_profile_mismatch",
    "timeout_model",
    "runtime_exception",
    "constraint_violation",
]

EPISODE_LOG_FIELDS = [
    "run_id",
    "track",
    "model_id",
    "model_family",
    "episode_id",
    "sequence_id",
    "initial_state_id",
    "condition_id",
    "scenario_profile_id",
    "action_level_id",
    "observation_profile_id",
    "pair_id",
    "baseline_episode_id",
    "subtasks_total",
    "subtasks_solved",
    "max_subtask_steps",
    "max_episode_steps",
    "max_time_sec",
    "status",
    "steps_total",
    "terminate_reason",
    "decision_time_total_sec",
    "predict_time_total_sec",
    "executor_time_total_sec",
    "env_step_time_total_sec",
    "oracle_check_time_total_sec",
    "episode_wallclock_total_sec",
    "started_at_utc",
    "finished_at_utc",
    "manifest_hash",
]

STEP_LOG_FIELDS = [
    "run_id",
    "episode_id",
    "step_idx",
    "subtask_idx",
    "timestamp_utc",
    "sequence_id",
    "condition_id",
    "scenario_profile_id",
    "action_level_id",
    "observation_profile_id",
    "pair_id",
    "current_instruction_text",
    "oracle_target_subtask",
    "decision_granularity",
    "active_modalities",
    "sensor_mask_before",
    "sensor_mask_after",
    "model_input_summary",
    "model_output_raw",
    "executor_applied_action",
    "model_status",
    "model_error_code",
    "model_error_message",
    "action_valid_flag",
    "oracle_success_current_step",
    "subtask_status",
    "episode_status",
    "subsequence_success_len",
    "noise_applied_flag",
    "safety_flag",
    "safety_reaction",
    "rollback_attempted",
    "rollback_success",
    "recovery_phase",
    "replan_event",
    "decision_time_ms",
    "predict_time_ms",
    "executor_time_ms",
    "env_step_time_ms",
    "oracle_check_time_ms",
    "wallclock_step_time_ms",
    "budget_left_subtask_steps",
    "budget_left_episode_steps",
    "budget_left_time_ms",
    "termination_reason",
    "notes",
]

EVENT_LOG_FIELDS = [
    "run_id",
    "episode_id",
    "event_id",
    "step_idx",
    "subtask_idx",
    "timestamp_utc",
    "sequence_id",
    "condition_id",
    "scenario_profile_id",
    "action_level_id",
    "pair_id",
    "event_type",
    "event_source",
    "event_payload_json",
    "noise_applied_flag",
    "resolved_flag",
    "resolve_step_idx",
    "resolve_latency_steps",
    "reaction_type",
]

ALL_SENSOR_CHANNELS = [
    "rgb_static",
    "rgb_gripper",
    "rgb_tactile",
    "depth_static",
    "depth_gripper",
    "depth_tactile",
    "robot_obs",
    "scene_obs",
]

MAX_TIME_PER_STEP_SEC = 2


@dataclass(frozen=True)
class ConditionDef:
    condition_id: str
    condition_id_seeded: str
    action_level_id: str
    action_repr: str
    scenario_profile_id: str
    observation_profile_id: str
    decision_granularity: str
    safety_mode: str
    termination_policy_id: str
    failure_policy_id: str
    active_channels: List[str]
    active_modalities: List[str]
    active_sensor_mask: Dict[str, int]
    action_dataset_keys: List[str]
    action_contract: Dict[str, Any]
    perturbation_contract: Dict[str, Any]


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "||".join(parts).encode("utf-8")
    digest = hashlib.sha1(payload).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _hash_int(token: str, modulo: int) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) % modulo


def _json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _write_csv(path: Path, fieldnames: List[str], rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_manifests(protocol_root: Path) -> Tuple[Dict[str, Any], Dict[str, Any], str, str]:
    manifest_dir = protocol_root / "manifest"
    task_manifest_path = manifest_dir / "task_manifest.json"
    benchmark_manifest_path = manifest_dir / "benchmark_manifest.json"
    task_manifest = _load_json(task_manifest_path)
    benchmark_manifest = _load_json(benchmark_manifest_path)
    return (
        task_manifest,
        benchmark_manifest,
        _sha256_file(task_manifest_path),
        _sha256_file(benchmark_manifest_path),
    )


def _parse_inline_list(value: str) -> List[str]:
    parsed = ast.literal_eval(value.strip())
    if not isinstance(parsed, list):
        raise ValueError(f"Expected list expression, got: {value}")
    return [str(item) for item in parsed]


def _load_observation_profile(path: Path) -> Dict[str, List[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Observation profile not found: {path}")
    data: Dict[str, List[str]] = {"rgb_obs": [], "depth_obs": [], "state_obs": [], "actions": [], "language": []}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if key in data:
            data[key] = _parse_inline_list(raw_value)
    return data


def _modalities_from_channels(channels: List[str], include_language: bool) -> List[str]:
    modalities: List[str] = []
    if any(ch.startswith("rgb_") for ch in channels):
        modalities.append("rgb")
    if any(ch.startswith("depth_") for ch in channels):
        modalities.append("depth")
    if any("tactile" in ch for ch in channels):
        modalities.append("tactile")
    if any(ch.endswith("_obs") for ch in channels):
        modalities.append("state")
    if include_language:
        modalities.append("language")
    return modalities


def _sensor_mask(channels: List[str]) -> Dict[str, int]:
    active = set(channels)
    return {channel: int(channel in active) for channel in ALL_SENSOR_CHANNELS}


def _action_contract(action_level_id: str, action_repr: str, dataset_action_keys: List[str]) -> Dict[str, Any]:
    if action_level_id == "L1":
        return {
            "action_schema": "symbolic_subtask_label",
            "expected_output": {"type": "string", "source": "subtask_list"},
            "native_mode": False,
            "native_note": "Alignment-layer symbolic mode.",
            "compatible_with_observation_profile": True,
        }
    if action_level_id == "L2":
        compatible = "actions" in dataset_action_keys
        return {
            "action_schema": "absolute_cartesian_tcp_7d",
            "expected_output": {
                "type": "vector",
                "size": 7,
                "fields": ["x", "y", "z", "euler_x", "euler_y", "euler_z", "gripper"],
                "dataset_key": "actions",
            },
            "native_mode": True,
            "compatible_with_observation_profile": compatible,
            "compatibility_note": "" if compatible else "Profile action key does not include 'actions'.",
        }
    if action_level_id == "L3":
        compatible = "rel_actions" in dataset_action_keys
        return {
            "action_schema": "relative_cartesian_7d",
            "expected_output": {
                "type": "vector",
                "size": 7,
                "fields": ["dx", "dy", "dz", "deuler_x", "deuler_y", "deuler_z", "gripper"],
                "dataset_key": "rel_actions",
            },
            "native_mode": True,
            "compatible_with_observation_profile": compatible,
            "compatibility_note": "" if compatible else "Profile action key does not include 'rel_actions'.",
        }
    if action_level_id == "L4":
        return {
            "action_schema": "joint_space_8d",
            "expected_output": {
                "type": "vector",
                "size": 8,
                "fields": [
                    "joint_1",
                    "joint_2",
                    "joint_3",
                    "joint_4",
                    "joint_5",
                    "joint_6",
                    "joint_7",
                    "gripper",
                ],
                "dataset_key": "joint_rel",
            },
            "native_mode": "declared",
            "native_note": "Declared by CALVIN README and RL notebook examples.",
            "compatible_with_observation_profile": True,
        }
    raise RuntimeError(f"Unknown action level: {action_level_id}")


def _perturbation_template(
    scenario_profile_id: str,
    active_channels: List[str],
    seed: int,
    condition_id: str,
) -> Dict[str, Any]:
    if scenario_profile_id == "ideal":
        return {"event_schedule_mode": "none", "events": []}

    if scenario_profile_id == "sensor_dropout":
        if not active_channels:
            return {
                "event_schedule_mode": "deterministic_seeded",
                "events": [{"event_type": "sensor_dropout", "payload": {"mode": "noop", "reason": "no_active_channels"}}],
            }
        idx = _hash_int(f"{seed}:{condition_id}:dropout", len(active_channels))
        channel = active_channels[idx]
        return {
            "event_schedule_mode": "deterministic_seeded",
            "events": [
                {"event_type": "sensor_dropout_partial", "payload": {"drop_channel": channel}},
                {"event_type": "sensor_dropout_whole_modality", "payload": {"base_channel": channel}},
            ],
        }

    if scenario_profile_id == "sensor_noise":
        noise_channels = [ch for ch in active_channels if ch.startswith("rgb_") or ch.startswith("depth_")]
        if not noise_channels:
            noise_channels = list(active_channels)
        return {
            "event_schedule_mode": "deterministic_seeded",
            "events": [
                {
                    "event_type": "sensor_noise",
                    "payload": {"target_channels": noise_channels, "noise_profile": "gaussian_sigma_0.03"},
                }
            ],
        }

    if scenario_profile_id == "safety_blackout_safe_abstain":
        return {
            "event_schedule_mode": "deterministic_seeded",
            "events": [{"event_type": "sensor_blackout", "payload": {"target_channels": active_channels}}],
        }

    if scenario_profile_id == "safety_blackout_best_effort":
        return {
            "event_schedule_mode": "deterministic_seeded",
            "events": [{"event_type": "sensor_blackout", "payload": {"target_channels": active_channels}}],
        }

    if scenario_profile_id == "recovery_wrong_action":
        return {
            "event_schedule_mode": "deterministic_seeded",
            "events": [
                {
                    "event_type": "injected_wrong_action",
                    "payload": {"mode": "contradict_target_subtask", "count": 5},
                }
            ],
        }

    if scenario_profile_id == "mixed_stress":
        return {
            "event_schedule_mode": "deterministic_seeded",
            "events": [
                {"event_type": "sensor_dropout_partial", "payload": {"mode": "deterministic"}},
                {"event_type": "sensor_noise", "payload": {"noise_profile": "gaussian_sigma_0.03"}},
                {"event_type": "injected_wrong_action", "payload": {"mode": "contradict_target_subtask", "count": 3}},
            ],
        }

    raise RuntimeError(f"Unknown scenario profile: {scenario_profile_id}")


def _channel_group(channel: str) -> str:
    if channel.startswith("rgb_"):
        return "rgb"
    if channel.startswith("depth_"):
        return "depth"
    if "tactile" in channel:
        return "tactile"
    if channel.endswith("_obs"):
        return "state"
    return "other"


def _episode_events(
    scenario_profile_id: str,
    active_channels: List[str],
    max_episode_steps: int,
    max_subtask_steps: int,
    subtasks_total: int,
    seed: int,
    episode_id: str,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []

    def add(event_type: str, start_step: int, end_step: int, payload: Dict[str, Any], subtask_idx: int = 0) -> None:
        events.append(
            {
                "event_id": f"evt_{len(events) + 1:03d}",
                "event_type": event_type,
                "event_source": "scenario",
                "start_step": start_step,
                "end_step": end_step,
                "subtask_idx": subtask_idx,
                "event_payload": payload,
            }
        )

    if scenario_profile_id == "ideal":
        return events

    if scenario_profile_id == "sensor_dropout":
        if not active_channels:
            add("sensor_dropout", 1, 1, {"mode": "noop", "reason": "no_active_channels"})
            return events
        channel = active_channels[_hash_int(f"{seed}:{episode_id}:dropout", len(active_channels))]
        midpoint = max(1, max_episode_steps // 2)
        add("sensor_dropout_partial", 1, midpoint, {"drop_channel": channel})
        same_group = [ch for ch in active_channels if _channel_group(ch) == _channel_group(channel)]
        add(
            "sensor_dropout_whole_modality",
            midpoint + 1,
            max_episode_steps,
            {"drop_group": _channel_group(channel), "drop_channels": same_group},
        )
        return events

    if scenario_profile_id == "sensor_noise":
        noise_channels = [ch for ch in active_channels if ch.startswith("rgb_") or ch.startswith("depth_")]
        if not noise_channels:
            noise_channels = list(active_channels)
        add("sensor_noise", 1, max_episode_steps, {"target_channels": noise_channels, "noise_profile": "gaussian_sigma_0.03"})
        return events

    if scenario_profile_id in {"safety_blackout_safe_abstain", "safety_blackout_best_effort"}:
        add("sensor_blackout", 1, max_episode_steps, {"target_channels": active_channels, "mode": "persistent"})
        return events

    if scenario_profile_id == "recovery_wrong_action":
        offset = max(2, max_subtask_steps // 4)
        for subtask_idx in range(1, subtasks_total + 1):
            step = min(max_episode_steps, (subtask_idx - 1) * max_subtask_steps + offset)
            add(
                "injected_wrong_action",
                step,
                step,
                {"mode": "contradict_target_subtask", "action_level_scope": "current"},
                subtask_idx=subtask_idx,
            )
        return events

    if scenario_profile_id == "mixed_stress":
        if active_channels:
            channel = active_channels[_hash_int(f"{seed}:{episode_id}:mixed_dropout", len(active_channels))]
            add("sensor_dropout_partial", 1, max_episode_steps // 2, {"drop_channel": channel})
        add("sensor_noise", 1, max_episode_steps, {"target_channels": active_channels, "noise_profile": "gaussian_sigma_0.03"})
        offset = max(2, max_subtask_steps // 3)
        for subtask_idx in range(1, subtasks_total + 1):
            step = min(max_episode_steps, (subtask_idx - 1) * max_subtask_steps + offset)
            add("injected_wrong_action", step, step, {"mode": "contradict_target_subtask"}, subtask_idx=subtask_idx)
        return events

    raise RuntimeError(f"Unknown scenario profile: {scenario_profile_id}")


def _build_conditions(
    benchmark_manifest: Dict[str, Any],
    calvin_root: Path,
) -> List[ConditionDef]:
    track = str(benchmark_manifest.get("track", "unified_ranking"))
    seed = int(benchmark_manifest.get("selection_seed", 42))
    action_levels = list(benchmark_manifest.get("action_levels", []))
    scenario_profiles = list(benchmark_manifest.get("scenario_profiles", []))
    observation_profiles = list(benchmark_manifest.get("observation_profiles", []))
    if not action_levels or not scenario_profiles or not observation_profiles:
        raise RuntimeError("benchmark_manifest must include action_levels, scenario_profiles, observation_profiles")

    obs_root = calvin_root / "calvin_models" / "conf" / "datamodule" / "observation_space"
    conditions: List[ConditionDef] = []
    for action_level in action_levels:
        level_id = str(action_level["action_level_id"])
        action_repr = str(action_level["action_repr"])
        decision_granularity = "symbolic_subtask" if level_id == "L1" else "control_step"
        for scenario_profile in scenario_profiles:
            scenario_profile_id = str(scenario_profile)
            if scenario_profile_id == "safety_blackout_safe_abstain":
                safety_mode = "safe_abstain"
            elif scenario_profile_id == "safety_blackout_best_effort":
                safety_mode = "best_effort"
            else:
                safety_mode = "none"

            if scenario_profile_id == "recovery_wrong_action":
                failure_policy_id = "allow_recovery_and_replan"
            elif scenario_profile_id.startswith("safety_blackout"):
                failure_policy_id = "safety_first_policy"
            else:
                failure_policy_id = "default_failure_policy"

            termination_policy_id = "default_long_horizon_termination"
            for observation_profile_id in observation_profiles:
                observation_profile_id = str(observation_profile_id)
                profile_path = obs_root / f"{observation_profile_id}.yaml"
                profile = _load_observation_profile(profile_path)
                active_channels = profile["rgb_obs"] + profile["depth_obs"] + profile["state_obs"]
                active_modalities = _modalities_from_channels(
                    channels=active_channels,
                    include_language=bool(profile["language"]),
                )
                condition_id = f"{track}.{level_id}.{scenario_profile_id}.{observation_profile_id}"
                condition_id_seeded = f"{condition_id}.seed{seed}"
                action_contract = _action_contract(level_id, action_repr, profile["actions"])
                perturbation_contract = _perturbation_template(
                    scenario_profile_id=scenario_profile_id,
                    active_channels=active_channels,
                    seed=seed,
                    condition_id=condition_id,
                )

                conditions.append(
                    ConditionDef(
                        condition_id=condition_id,
                        condition_id_seeded=condition_id_seeded,
                        action_level_id=level_id,
                        action_repr=action_repr,
                        scenario_profile_id=scenario_profile_id,
                        observation_profile_id=observation_profile_id,
                        decision_granularity=decision_granularity,
                        safety_mode=safety_mode,
                        termination_policy_id=termination_policy_id,
                        failure_policy_id=failure_policy_id,
                        active_channels=active_channels,
                        active_modalities=active_modalities,
                        active_sensor_mask=_sensor_mask(active_channels),
                        action_dataset_keys=profile["actions"],
                        action_contract=action_contract,
                        perturbation_contract=perturbation_contract,
                    )
                )
    return conditions


def _build_condition_contract_json(condition: ConditionDef, max_subtask_steps: int, max_episode_steps: int) -> Dict[str, Any]:
    return {
        "condition_id": condition.condition_id,
        "condition_id_seeded": condition.condition_id_seeded,
        "action_level_id": condition.action_level_id,
        "action_repr": condition.action_repr,
        "scenario_profile_id": condition.scenario_profile_id,
        "observation_profile_id": condition.observation_profile_id,
        "decision_granularity": condition.decision_granularity,
        "observation_contract": {
            "active_channels": condition.active_channels,
            "active_modalities": condition.active_modalities,
            "active_sensor_mask_template": condition.active_sensor_mask,
            "single_sensor_comparison_rule": "compare only profiles that differ by exactly one channel",
            "required_step_payload_fields": STEP_PAYLOAD_FIELDS,
        },
        "action_contract": condition.action_contract,
        "model_contract": {
            "required_step_payload_fields": STEP_PAYLOAD_FIELDS,
            "required_model_response_fields": MODEL_RESPONSE_FIELDS,
            "allowed_model_error_codes": MODEL_ERROR_CODES,
        },
        "runtime_contract": {
            "termination_policy_id": condition.termination_policy_id,
            "failure_policy_id": condition.failure_policy_id,
            "safety_mode": condition.safety_mode,
            "max_subtask_steps": max_subtask_steps,
            "max_episode_steps": max_episode_steps,
        },
        "environment_contract": {
            "backend": "pybullet",
            "control_freq_hz": 30,
            "show_gui": False,
            "subtasks_per_sequence": 5,
        },
        "perturbation_contract": condition.perturbation_contract,
    }


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
        manifest_data = json.loads(scenario_manifest_path.read_text(encoding="utf-8"))
        return {
            "status": "skipped_existing",
            "scenario_contract_manifest": str(scenario_manifest_path),
            "run_signature": manifest_data.get("run_signature"),
            "conditions_total": manifest_data.get("conditions_total"),
            "episodes_total": manifest_data.get("episodes_total"),
            "steps_total": manifest_data.get("steps_total"),
            "events_total": manifest_data.get("events_total"),
        }

    task_manifest, benchmark_manifest, task_manifest_sha, benchmark_manifest_sha = _load_manifests(protocol_root)
    tasks = list(task_manifest.get("tasks", []))
    if not tasks:
        raise RuntimeError("task_manifest has no selected scenarios")

    calvin_root = Path(task_manifest.get("calvin_root", ""))
    if not calvin_root.exists():
        raise RuntimeError("task_manifest.calvin_root is missing or invalid")

    selection_seed = int(task_manifest.get("selection_seed", benchmark_manifest.get("selection_seed", 42)))
    track = str(benchmark_manifest.get("track", "unified_ranking"))
    max_subtask_steps = int(task_manifest.get("reference", {}).get("ep_len", 360))
    subtasks_per_sequence = int(task_manifest.get("reference", {}).get("seq_len", 5))
    max_episode_steps = max_subtask_steps * subtasks_per_sequence
    max_time_sec = max_episode_steps * MAX_TIME_PER_STEP_SEC

    conditions = _build_conditions(benchmark_manifest=benchmark_manifest, calvin_root=calvin_root)
    condition_contracts = [
        _build_condition_contract_json(
            condition=condition,
            max_subtask_steps=max_subtask_steps,
            max_episode_steps=max_episode_steps,
        )
        for condition in conditions
    ]

    condition_lookup: Dict[Tuple[str, str, str], ConditionDef] = {
        (c.action_level_id, c.scenario_profile_id, c.observation_profile_id): c for c in conditions
    }

    episodes_rows: List[Dict[str, Any]] = []
    steps_rows: List[Dict[str, Any]] = []
    events_rows: List[Dict[str, Any]] = []

    for scenario in tasks:
        sequence_id = str(scenario["sequence_id"])
        initial_state_id = str(scenario["initial_state_id"])
        subtasks_total = int(scenario.get("sequence_length", subtasks_per_sequence))
        subtask_list = list(scenario["subtask_list"])
        instruction_texts = list(scenario["instruction_texts"])
        initial_state = dict(scenario["initial_state"])

        for condition in conditions:
            episode_id = _stable_id("ep", track, sequence_id, condition.condition_id, str(selection_seed))
            pair_id = _stable_id(
                "pair",
                track,
                sequence_id,
                condition.action_level_id,
                condition.observation_profile_id,
                str(selection_seed),
            )
            baseline_condition = condition_lookup[
                (condition.action_level_id, "ideal", condition.observation_profile_id)
            ]
            baseline_episode_id = _stable_id(
                "ep",
                track,
                sequence_id,
                baseline_condition.condition_id,
                str(selection_seed),
            )

            perturbation_schedule_id = _stable_id(
                "sched",
                episode_id,
                condition.scenario_profile_id,
                str(selection_seed),
            )

            episode_events = _episode_events(
                scenario_profile_id=condition.scenario_profile_id,
                active_channels=condition.active_channels,
                max_episode_steps=max_episode_steps,
                max_subtask_steps=max_subtask_steps,
                subtasks_total=subtasks_total,
                seed=selection_seed,
                episode_id=episode_id,
            )

            dropped_policy = {
                "enabled": condition.scenario_profile_id in {"sensor_dropout", "mixed_stress"},
                "mode": "deterministic_profile_schedule",
            }
            noise_profile = {
                "enabled": condition.scenario_profile_id in {"sensor_noise", "mixed_stress"},
                "profile_id": "gaussian_sigma_0.03" if condition.scenario_profile_id in {"sensor_noise", "mixed_stress"} else "none",
            }
            safety_contract = {
                "safety_mode": condition.safety_mode,
                "strict_stop_required": condition.safety_mode == "safe_abstain",
            }

            episodes_rows.append(
                {
                    "episode_id": episode_id,
                    "pair_id": pair_id,
                    "baseline_episode_id": baseline_episode_id,
                    "sequence_id": sequence_id,
                    "initial_state_id": initial_state_id,
                    "condition_id": condition.condition_id,
                    "condition_id_seeded": condition.condition_id_seeded,
                    "track": track,
                    "selection_seed": selection_seed,
                    "action_level_id": condition.action_level_id,
                    "scenario_profile_id": condition.scenario_profile_id,
                    "observation_profile_id": condition.observation_profile_id,
                    "decision_granularity": condition.decision_granularity,
                    "safety_mode": condition.safety_mode,
                    "termination_policy_id": condition.termination_policy_id,
                    "failure_policy_id": condition.failure_policy_id,
                    "perturbation_schedule_id": perturbation_schedule_id,
                    "subtasks_total": subtasks_total,
                    "max_subtask_steps": max_subtask_steps,
                    "max_episode_steps": max_episode_steps,
                    "max_time_sec": max_time_sec,
                    "active_modalities_json": _json_cell(condition.active_modalities),
                    "active_sensor_mask_json": _json_cell(condition.active_sensor_mask),
                    "active_channels_json": _json_cell(condition.active_channels),
                    "subtask_list_json": _json_cell(subtask_list),
                    "instruction_texts_json": _json_cell(instruction_texts),
                    "initial_state_json": _json_cell(initial_state),
                }
            )

            steps_rows.append(
                {
                    "episode_id": episode_id,
                    "condition_id": condition.condition_id,
                    "sequence_id": sequence_id,
                    "action_level_id": condition.action_level_id,
                    "scenario_profile_id": condition.scenario_profile_id,
                    "observation_profile_id": condition.observation_profile_id,
                    "step_idx_start": 1,
                    "step_idx_end": max_episode_steps,
                    "decision_granularity": condition.decision_granularity,
                    "required_step_payload_fields_json": _json_cell(STEP_PAYLOAD_FIELDS),
                    "required_model_response_fields_json": _json_cell(MODEL_RESPONSE_FIELDS),
                    "allowed_model_error_codes_json": _json_cell(MODEL_ERROR_CODES),
                    "required_step_log_fields_json": _json_cell(STEP_LOG_FIELDS),
                    "required_event_log_fields_json": _json_cell(EVENT_LOG_FIELDS),
                    "active_modalities_json": _json_cell(condition.active_modalities),
                    "active_sensor_mask_template_json": _json_cell(condition.active_sensor_mask),
                    "dropped_modalities_policy_json": _json_cell(dropped_policy),
                    "noise_profile_json": _json_cell(noise_profile),
                    "safety_contract_json": _json_cell(safety_contract),
                    "event_context_schema_json": _json_cell(
                        {
                            "event_id": "string",
                            "event_type": "string",
                            "event_source": "string",
                            "payload": "object",
                        }
                    ),
                    "history_policy_json": _json_cell({"history_actions": "append_only", "history_events": "append_only"}),
                    "budget_policy_json": _json_cell(
                        {
                            "max_subtask_steps": max_subtask_steps,
                            "max_episode_steps": max_episode_steps,
                            "max_time_sec": max_time_sec,
                        }
                    ),
                }
            )

            for event in episode_events:
                events_rows.append(
                    {
                        "episode_id": episode_id,
                        "condition_id": condition.condition_id,
                        "sequence_id": sequence_id,
                        "action_level_id": condition.action_level_id,
                        "scenario_profile_id": condition.scenario_profile_id,
                        "observation_profile_id": condition.observation_profile_id,
                        "pair_id": pair_id,
                        "event_id": event["event_id"],
                        "event_type": event["event_type"],
                        "event_source": event["event_source"],
                        "start_step": event["start_step"],
                        "end_step": event["end_step"],
                        "subtask_idx": event["subtask_idx"],
                        "event_payload_json": _json_cell(event["event_payload"]),
                        "safety_mode": condition.safety_mode,
                        "perturbation_schedule_id": perturbation_schedule_id,
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
            "baseline_episode_id",
            "sequence_id",
            "initial_state_id",
            "condition_id",
            "condition_id_seeded",
            "track",
            "selection_seed",
            "action_level_id",
            "scenario_profile_id",
            "observation_profile_id",
            "decision_granularity",
            "safety_mode",
            "termination_policy_id",
            "failure_policy_id",
            "perturbation_schedule_id",
            "subtasks_total",
            "max_subtask_steps",
            "max_episode_steps",
            "max_time_sec",
            "active_modalities_json",
            "active_sensor_mask_json",
            "active_channels_json",
            "subtask_list_json",
            "instruction_texts_json",
            "initial_state_json",
        ],
        rows=episodes_rows,
    )

    _write_csv(
        steps_path,
        fieldnames=[
            "episode_id",
            "condition_id",
            "sequence_id",
            "action_level_id",
            "scenario_profile_id",
            "observation_profile_id",
            "step_idx_start",
            "step_idx_end",
            "decision_granularity",
            "required_step_payload_fields_json",
            "required_model_response_fields_json",
            "allowed_model_error_codes_json",
            "required_step_log_fields_json",
            "required_event_log_fields_json",
            "active_modalities_json",
            "active_sensor_mask_template_json",
            "dropped_modalities_policy_json",
            "noise_profile_json",
            "safety_contract_json",
            "event_context_schema_json",
            "history_policy_json",
            "budget_policy_json",
        ],
        rows=steps_rows,
    )

    _write_csv(
        events_path,
        fieldnames=[
            "episode_id",
            "condition_id",
            "sequence_id",
            "action_level_id",
            "scenario_profile_id",
            "observation_profile_id",
            "pair_id",
            "event_id",
            "event_type",
            "event_source",
            "start_step",
            "end_step",
            "subtask_idx",
            "event_payload_json",
            "safety_mode",
            "perturbation_schedule_id",
        ],
        rows=events_rows,
    )

    output_hashes = {
        "conditions_contracts.json": _sha256_file(conditions_path),
        "schema_refs.json": _sha256_file(schema_refs_path),
        "episodes_contracts.csv": _sha256_file(episodes_path),
        "steps_contracts.csv": _sha256_file(steps_path),
        "events_schedule.csv": _sha256_file(events_path),
    }

    run_signature = hashlib.sha256(
        json.dumps(
            {
                "task_manifest_sha256": task_manifest_sha,
                "benchmark_manifest_sha256": benchmark_manifest_sha,
                "selection_seed": selection_seed,
                "track": track,
                "max_subtask_steps": max_subtask_steps,
                "max_episode_steps": max_episode_steps,
                "max_time_per_step_sec": MAX_TIME_PER_STEP_SEC,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    scenario_manifest = {
        "schema_version": "v1",
        "protocol_version": "calvin_stage2",
        "generated_from": {
            "task_manifest": str((protocol_root / "manifest" / "task_manifest.json").resolve()),
            "benchmark_manifest": str((protocol_root / "manifest" / "benchmark_manifest.json").resolve()),
            "task_manifest_sha256": task_manifest_sha,
            "benchmark_manifest_sha256": benchmark_manifest_sha,
        },
        "run_signature": run_signature,
        "track": track,
        "selection_seed": selection_seed,
        "conditions_total": len(condition_contracts),
        "episodes_total": len(episodes_rows),
        "steps_total": len(steps_rows),
        "events_total": len(events_rows),
        "budget_policy": {
            "max_subtask_steps": max_subtask_steps,
            "max_episode_steps": max_episode_steps,
            "max_time_sec_formula": f"{max_episode_steps}*{MAX_TIME_PER_STEP_SEC}",
        },
        "assumptions": [
            "Step 2 generates machine-readable contracts per episode, not expanded runtime logs.",
            "steps_contracts.csv is per-episode step template (step range), not per-step rollout trace.",
            "Noise/dropout/blackout/wrong-action schedules are deterministic and seed-controlled.",
        ],
        "output_files": {
            "conditions_contracts": str(conditions_path),
            "schema_refs": str(schema_refs_path),
            "episodes_contracts_csv": str(episodes_path),
            "steps_contracts_csv": str(steps_path),
            "events_schedule_csv": str(events_path),
        },
        "output_file_hashes_sha256": output_hashes,
    }
    scenario_manifest_path.write_text(json.dumps(scenario_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

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
        description="Build CALVIN scenario contracts from protocol bundle manifests."
    )
    parser.add_argument(
        "--protocol-root",
        default="calvin_bench/estimate_scripts/protocol_bundle",
        help="Path to protocol bundle produced by step 1.",
    )
    parser.add_argument(
        "--output-root",
        default="calvin_bench/estimate_scripts/protocol_bundle/contracts",
        help="Output path for generated scenario contracts.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing scenario contracts.",
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
