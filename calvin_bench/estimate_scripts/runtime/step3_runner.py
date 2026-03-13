import csv
import hashlib
import json
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import cv2
import numpy as np

from .benchmark_logging import (
    ensure_dir,
    write_condition_cells,
    write_episode_index,
    write_flat_logs,
    write_frames_manifest,
)
from .model_adapter import BaseModelAdapter, build_model_adapter
from .protocol_runtime import (
    action_to_env_command,
    active_events_at_step,
    apply_protocol_to_observation,
    build_model_observation_bundle,
    canonicalize_model_action,
    deterministic_wrong_action,
)
from .scenario_loader import EpisodeContract, EventSpec, ProtocolContracts, load_protocol_contracts


@dataclass
class WorkerOutput:
    episodes: List[Dict[str, Any]]
    steps: List[Dict[str, Any]]
    events: List[Dict[str, Any]]
    frames: List[Dict[str, Any]]
    errors: List[str]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"calvin_step3_{stamp}"


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _copy_manifest_inputs(run_manifest_dir: Path, protocol_root: Path, contracts_root: Path) -> Dict[str, str]:
    ensure_dir(run_manifest_dir)
    src_files = [
        protocol_root / "manifest" / "task_manifest.json",
        protocol_root / "manifest" / "task_manifest.sha256",
        protocol_root / "manifest" / "benchmark_manifest.json",
        protocol_root / "manifest" / "benchmark_manifest.sha256",
        contracts_root / "scenario_contract_manifest.json",
        contracts_root / "conditions_contracts.json",
        contracts_root / "episodes_contracts.csv",
        contracts_root / "steps_contracts.csv",
        contracts_root / "events_schedule.csv",
        contracts_root / "schema_refs.json",
    ]
    hashes: Dict[str, str] = {}
    for src in src_files:
        if not src.exists():
            raise FileNotFoundError(f"Missing required artifact: {src}")
        dst = run_manifest_dir / src.name
        shutil.copy2(src, dst)
        if dst.suffix in (".json", ".csv"):
            hashes[dst.name] = _sha256_file(dst)
    return hashes


def _split_by_worker(episodes: List[EpisodeContract], workers: int) -> Dict[int, List[EpisodeContract]]:
    out = {idx: [] for idx in range(workers)}
    for idx, episode in enumerate(episodes):
        out[idx % workers].append(episode)
    return out


def _counts_by_field(episodes: List[EpisodeContract], key: Callable[[EpisodeContract], str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for episode in episodes:
        tag = key(episode)
        out[tag] = out.get(tag, 0) + 1
    return dict(sorted(out.items(), key=lambda x: x[0]))


def _setup_calvin_imports(calvin_root: Path) -> None:
    calvin_root = calvin_root.resolve()
    extra_paths = [
        calvin_root / "calvin_models",
        calvin_root / "calvin_env",
    ]
    for path in extra_paths:
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def _initial_state_seed(initial_condition: Dict[str, Any]) -> int:
    payload = json.dumps(initial_condition, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:8], 16)


def _get_env_state_for_initial_condition(initial_condition: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
    robot_obs = np.array(
        [
            0.02586889,
            -0.2313129,
            0.5712808,
            3.09045411,
            -0.02908596,
            1.50013585,
            0.07999963,
            -1.21779124,
            1.03987629,
            2.11978254,
            -2.34205014,
            -0.87015899,
            1.64119093,
            0.55344928,
            1.0,
        ],
        dtype=np.float32,
    )
    block_rot_z_range = (np.pi / 2 - np.pi / 8, np.pi / 2 + np.pi / 8)
    block_slider_left = np.array([-2.40851662e-01, 9.24044687e-02, 4.60990009e-01], dtype=np.float32)
    block_slider_right = np.array([7.03416330e-02, 9.24044687e-02, 4.60990009e-01], dtype=np.float32)
    block_table = [
        np.array([5.00000896e-02, -1.20000177e-01, 4.59990009e-01], dtype=np.float32),
        np.array([2.29995412e-01, -1.19995140e-01, 4.59990010e-01], dtype=np.float32),
    ]
    rng = np.random.default_rng(_initial_state_seed(initial_condition))
    rng.shuffle(block_table)

    scene_obs = np.zeros(24, dtype=np.float32)
    if initial_condition.get("slider") == "left":
        scene_obs[0] = 0.28
    if initial_condition.get("drawer") == "open":
        scene_obs[1] = 0.22
    if int(initial_condition.get("lightbulb", 0)) == 1:
        scene_obs[3] = 0.088
    scene_obs[4] = int(initial_condition.get("lightbulb", 0))
    scene_obs[5] = int(initial_condition.get("led", 0))

    def _set_block(offset: int, block_key: str, fallback_table_idx: int) -> None:
        place = str(initial_condition.get(block_key, "table"))
        if place == "slider_right":
            scene_obs[offset : offset + 3] = block_slider_right
        elif place == "slider_left":
            scene_obs[offset : offset + 3] = block_slider_left
        else:
            scene_obs[offset : offset + 3] = block_table[fallback_table_idx]

    _set_block(6, "red_block", 0)
    scene_obs[11] = float(rng.uniform(*block_rot_z_range))

    if str(initial_condition.get("blue_block", "table")) == "table" and str(initial_condition.get("red_block", "table")) == "table":
        blue_fallback_idx = 1
    else:
        blue_fallback_idx = 0
    _set_block(12, "blue_block", blue_fallback_idx)
    scene_obs[17] = float(rng.uniform(*block_rot_z_range))

    _set_block(18, "pink_block", 1)
    scene_obs[23] = float(rng.uniform(*block_rot_z_range))
    return robot_obs, scene_obs


def _instantiate_env_and_oracle(calvin_root: Path, dataset_path: Path, show_gui: bool) -> Tuple[Any, Any]:
    _setup_calvin_imports(calvin_root)
    import hydra
    from omegaconf import OmegaConf

    from calvin_env.envs.play_table_env import get_env

    conf_dir = calvin_root / "calvin_models" / "conf"
    task_cfg = OmegaConf.load(conf_dir / "callbacks" / "rollout" / "tasks" / "new_playtable_tasks.yaml")
    task_oracle = hydra.utils.instantiate(task_cfg)
    dataset_root = dataset_path.resolve()
    val_folder = dataset_root / "validation"
    env_source = val_folder if val_folder.exists() else dataset_root
    env = get_env(env_source, show_gui=show_gui)
    return env, task_oracle


def _save_frame(frame: np.ndarray, out_dir: Path, step_idx: int) -> Tuple[Path | None, str]:
    frame_hash = hashlib.md5(frame.tobytes()).hexdigest()
    frame_path = out_dir / f"step_{step_idx:04d}.png"
    ok = cv2.imwrite(str(frame_path), frame[:, :, ::-1] if frame.ndim == 3 else frame)
    if not ok:
        return None, ""
    return frame_path, frame_hash


def _select_frame(protocol_obs: Dict[str, Any], raw_obs: Dict[str, Any]) -> Tuple[np.ndarray | None, str]:
    channels = protocol_obs.get("channels_filtered", {})
    for key in ("rgb_static", "rgb_gripper", "rgb_tactile"):
        value = channels.get(key)
        if isinstance(value, np.ndarray) and value.ndim == 3:
            return value, key
    rgb_obs = raw_obs.get("rgb_obs", {})
    if isinstance(rgb_obs, dict):
        for key in ("rgb_static", "rgb_gripper"):
            value = rgb_obs.get(key)
            if isinstance(value, np.ndarray) and value.ndim == 3:
                return value, f"raw_{key}"
    return None, "none"


def _build_event_rows(
    run_id: str,
    episode: EpisodeContract,
    step_idx: int,
    subtask_idx: int,
    active_events: List[EventSpec],
    noise_applied_flag: bool,
    reaction_type: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for event in active_events:
        resolved = step_idx >= event.end_step
        rows.append(
            {
                "run_id": run_id,
                "episode_id": episode.episode_id,
                "event_id": event.event_id,
                "step_idx": step_idx,
                "subtask_idx": subtask_idx,
                "timestamp_utc": _utc_now(),
                "sequence_id": episode.sequence_id,
                "condition_id": episode.condition_id,
                "scenario_profile_id": episode.scenario_profile_id,
                "action_level_id": episode.action_level_id,
                "pair_id": episode.pair_id,
                "event_type": event.event_type,
                "event_source": event.event_source,
                "event_payload_json": _safe_json(event.payload),
                "noise_applied_flag": str(bool(noise_applied_flag)).lower(),
                "resolved_flag": str(bool(resolved)).lower(),
                "resolve_step_idx": step_idx if resolved else "",
                "resolve_latency_steps": (step_idx - event.start_step) if resolved else "",
                "reaction_type": reaction_type,
            }
        )
    return rows


def _run_episode(
    run_id: str,
    model_id: str,
    model_family: str,
    episode: EpisodeContract,
    contracts: ProtocolContracts,
    model_adapter: BaseModelAdapter,
    env: Any,
    task_oracle: Any,
    manifest_hash: str,
    save_frames: bool,
    frames_root: Path,
    model_backend: str,
    allow_subtask_skip: bool,
    allow_incompatible_conditions: bool,
) -> WorkerOutput:
    episodes_rows: List[Dict[str, Any]] = []
    steps_rows: List[Dict[str, Any]] = []
    events_rows: List[Dict[str, Any]] = []
    frames_rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    condition = contracts.conditions[episode.condition_id]
    compatible = bool(condition.action_contract.get("compatible_with_observation_profile", True))
    started_utc = _utc_now()
    started_perf = time.perf_counter()

    if not compatible and not allow_incompatible_conditions:
        episodes_rows.append(
            {
                "run_id": run_id,
                "track": episode.track,
                "model_id": model_id,
                "model_family": model_family,
                "episode_id": episode.episode_id,
                "sequence_id": episode.sequence_id,
                "initial_state_id": episode.initial_state_id,
                "condition_id": episode.condition_id,
                "scenario_profile_id": episode.scenario_profile_id,
                "action_level_id": episode.action_level_id,
                "observation_profile_id": episode.observation_profile_id,
                "pair_id": episode.pair_id,
                "baseline_episode_id": episode.baseline_episode_id,
                "subtasks_total": episode.subtasks_total,
                "subtasks_solved": 0,
                "max_subtask_steps": episode.max_subtask_steps,
                "max_episode_steps": episode.max_episode_steps,
                "max_time_sec": episode.max_time_sec,
                "status": "unsupported",
                "steps_total": 0,
                "terminate_reason": "incompatible_condition",
                "decision_time_total_sec": "0.000000",
                "predict_time_total_sec": "0.000000",
                "executor_time_total_sec": "0.000000",
                "env_step_time_total_sec": "0.000000",
                "oracle_check_time_total_sec": "0.000000",
                "episode_wallclock_total_sec": "0.000000",
                "started_at_utc": started_utc,
                "finished_at_utc": _utc_now(),
                "manifest_hash": manifest_hash,
            }
        )
        return WorkerOutput(episodes_rows, steps_rows, events_rows, frames_rows, errors)

    robot_obs, scene_obs = _get_env_state_for_initial_condition(episode.initial_state)
    obs = env.reset(robot_obs=robot_obs, scene_obs=scene_obs)
    model_adapter.reset(
        {
            "run_id": run_id,
            "track": episode.track,
            "episode_id": episode.episode_id,
            "sequence_id": episode.sequence_id,
            "initial_state_id": episode.initial_state_id,
            "condition_id": episode.condition_id,
            "selection_seed": episode.selection_seed,
            "action_level_id": episode.action_level_id,
            "scenario_profile_id": episode.scenario_profile_id,
            "observation_profile_id": episode.observation_profile_id,
            "pair_id": episode.pair_id,
            "baseline_episode_id": episode.baseline_episode_id,
            "subtasks_total": episode.subtasks_total,
            "max_subtask_steps": episode.max_subtask_steps,
            "max_episode_steps": episode.max_episode_steps,
            "max_time_sec": episode.max_time_sec,
            "termination_policy_id": episode.termination_policy_id,
            "failure_policy_id": episode.failure_policy_id,
            "safety_mode": episode.safety_mode,
            "perturbation_schedule_id": episode.perturbation_schedule_id,
            "manifest_hash": manifest_hash,
        }
    )

    history_actions: List[str] = []
    history_events: List[str] = []
    decision_total = 0.0
    predict_total = 0.0
    executor_total = 0.0
    env_step_total = 0.0
    oracle_total = 0.0
    steps_total = 0
    subtasks_solved = 0
    subtask_idx = 1
    steps_in_subtask = 0
    status = "running"
    terminate_reason = ""
    subtask_start_info = env.get_info()
    all_events = contracts.events_by_episode.get(episode.episode_id, [])
    subtask_candidates = list(episode.subtask_list)

    for step_idx in range(1, episode.max_episode_steps + 1):
        episode_elapsed = time.perf_counter() - started_perf
        if episode_elapsed > episode.max_time_sec:
            status = "timeout"
            terminate_reason = "max_time_reached"
            break
        if subtask_idx > episode.subtasks_total:
            status = "success"
            terminate_reason = "success_all_subtasks"
            break

        current_subtask = episode.subtask_list[subtask_idx - 1]
        current_instruction = episode.instruction_texts[subtask_idx - 1]
        active_events = active_events_at_step(all_events, step_idx=step_idx, subtask_idx=subtask_idx)

        protocol_obs = apply_protocol_to_observation(
            raw_obs=obs,
            base_sensor_mask=episode.active_sensor_mask,
            active_events=active_events,
            episode_id=episode.episode_id,
            step_idx=step_idx,
        )
        observation_bundle, observation_bundle_raw, observation_summary = build_model_observation_bundle(
            protocol_obs["channels_filtered"]
        )

        safety_flag = any(ev.event_type == "sensor_blackout" for ev in active_events)
        event_context = [
            {
                "event_id": ev.event_id,
                "event_type": ev.event_type,
                "event_source": ev.event_source,
                "start_step": ev.start_step,
                "end_step": ev.end_step,
                "payload": ev.payload,
            }
            for ev in active_events
        ]
        step_payload: Dict[str, Any] = {
            "run_id": run_id,
            "episode_id": episode.episode_id,
            "step_idx": step_idx,
            "subtask_idx": subtask_idx,
            "current_instruction_text": current_instruction,
            "oracle_target_subtask": current_subtask,
            "action_level_id": episode.action_level_id,
            "scenario_profile_id": episode.scenario_profile_id,
            "observation_profile_id": episode.observation_profile_id,
            "active_modalities": protocol_obs["active_modalities"],
            "active_sensor_mask": protocol_obs["sensor_mask_after"],
            "dropped_modalities": protocol_obs["dropped_modalities"],
            "noise_profile": protocol_obs["noise_profile"],
            "safety_contract": {
                "safety_mode": episode.safety_mode,
                "strict_stop_required": episode.safety_mode == "safe_abstain",
            },
            "event_context": event_context,
            "observation_bundle": observation_bundle,
            "history_actions": history_actions[-50:],
            "history_events": history_events[-50:],
            "budget_left_subtask_steps": max(0, episode.max_subtask_steps - steps_in_subtask),
            "budget_left_episode_steps": max(0, episode.max_episode_steps - step_idx + 1),
            "budget_left_time_sec": max(0.0, episode.max_time_sec - episode_elapsed),
            "active_constraints": {
                "allowed_symbolic_subtasks": subtask_candidates,
                "action_level_id": episode.action_level_id,
                "safety_mode": episode.safety_mode,
            },
            "observation_bundle_raw": observation_bundle_raw,
        }
        model_payload = dict(step_payload)
        if model_backend == "http":
            model_payload.pop("observation_bundle_raw", None)

        decision_started = time.perf_counter()
        model_resp = model_adapter.predict(model_payload)
        decision_time = time.perf_counter() - decision_started
        predict_time = float(model_resp.get("model_latency_sec", 0.0) or 0.0)
        if predict_time <= 0:
            predict_time = decision_time
        decision_total += decision_time
        predict_total += predict_time

        model_status = str(model_resp.get("status", "error") or "error")
        model_error_code = str(model_resp.get("error_code", "") or "")
        model_error_message = str(model_resp.get("error_message", "") or "")
        safety_intent = str(model_resp.get("safety_intent", "") or "")
        fallback_mode = str(model_resp.get("fallback_mode", "") or "")
        replan_requested = bool(model_resp.get("replan_requested", False))
        rollback_requested = bool(model_resp.get("rollback_requested", False))
        action_raw = model_resp.get("action_raw", "")
        action_candidate = model_resp.get("action_exec", action_raw)
        action_valid = False
        action_exec = action_candidate
        if model_status == "ok":
            action_valid, action_exec, action_err = canonicalize_model_action(episode.action_level_id, action_candidate)
            if not action_valid:
                model_status = "error"
                model_error_code = action_err
                model_error_message = action_err

        replan_event = bool(replan_requested)
        rollback_attempted = bool(rollback_requested)
        rollback_success = False
        safety_reaction = "none"
        recovery_phase = "none"
        executor_applied_action = action_exec

        if safety_flag and episode.safety_mode == "safe_abstain":
            wants_abstain = safety_intent.strip().lower() in {"abstain", "stop", "safe_abstain", "fallback"}
            if wants_abstain or not action_valid:
                safety_reaction = "safe_abstain_stop"
                status = "safety_stop"
                terminate_reason = "safety_blackout_safe_abstain"
            else:
                safety_reaction = "unsafe_continue_detected"
                model_status = "error"
                model_error_code = "constraint_violation"
                model_error_message = "safe_abstain required during blackout"
                status = "failed"
                terminate_reason = "safety_contract_violation"

        wrong_action_active = any(ev.event_type == "injected_wrong_action" for ev in active_events)
        if wrong_action_active and model_status == "ok":
            recovery_phase = "forced_deviation"
            replan_event = True
            executor_applied_action = deterministic_wrong_action(
                action_level_id=episode.action_level_id,
                episode_id=episode.episode_id,
                step_idx=step_idx,
                target_subtask=current_subtask,
                subtask_candidates=subtask_candidates,
            )
            history_events.append("event:injected_wrong_action")

        success_current_step = False
        current_info = env.get_info()
        oracle_elapsed = 0.0
        env_step_elapsed = 0.0
        executor_elapsed = 0.0

        if status == "running" and model_status == "ok" and action_valid:
            if episode.action_level_id == "L1":
                executor_started = time.perf_counter()
                success_current_step = str(executor_applied_action) == current_subtask
                executor_elapsed = time.perf_counter() - executor_started
                if wrong_action_active:
                    success_current_step = False
                current_info = env.get_info()
            else:
                ok_cmd, env_cmd, cmd_err = action_to_env_command(episode.action_level_id, executor_applied_action)
                if not ok_cmd:
                    model_status = "error"
                    model_error_code = cmd_err
                    model_error_message = cmd_err
                else:
                    executor_started = time.perf_counter()
                    env_started = time.perf_counter()
                    obs, _, _, current_info = env.step(env_cmd)
                    env_step_elapsed = time.perf_counter() - env_started
                    executor_elapsed = time.perf_counter() - executor_started
                    oracle_started = time.perf_counter()
                    current_task_info = task_oracle.get_task_info_for_set(subtask_start_info, current_info, {current_subtask})
                    success_current_step = len(current_task_info) > 0
                    oracle_elapsed = time.perf_counter() - oracle_started

        executor_total += executor_elapsed
        env_step_total += env_step_elapsed
        oracle_total += oracle_elapsed
        steps_total += 1

        if model_status != "ok":
            history_actions.append(f"MODEL_ERROR:{model_error_code or 'unknown'}")
            if status == "running":
                status = "failed"
                terminate_reason = f"model_{model_error_code or 'runtime_error'}"
        else:
            history_actions.append(str(executor_applied_action))

        subtask_status = "ongoing"
        if status == "running":
            if success_current_step:
                subtasks_solved += 1
                subtask_status = "success"
                subtask_idx += 1
                steps_in_subtask = 0
                subtask_start_info = current_info
                recovery_phase = "resume" if wrong_action_active else recovery_phase
                if subtask_idx > episode.subtasks_total:
                    status = "success"
                    terminate_reason = "success_all_subtasks"
            else:
                steps_in_subtask += 1
                if steps_in_subtask >= episode.max_subtask_steps:
                    if allow_subtask_skip:
                        subtask_status = "failed_skipped"
                        subtask_idx += 1
                        steps_in_subtask = 0
                        subtask_start_info = current_info
                        history_events.append("runtime:subtask_skipped_budget")
                        recovery_phase = "replan" if wrong_action_active else recovery_phase
                    else:
                        status = "failed"
                        terminate_reason = "subtask_budget_exhausted"
                        subtask_status = "failed"

        episode_status = status if status != "running" else "running"
        termination_reason_step = terminate_reason if status != "running" else ""
        budget_left_subtask = max(0, episode.max_subtask_steps - steps_in_subtask)
        budget_left_episode = max(0, episode.max_episode_steps - step_idx)
        budget_left_time_ms = max(0, int((episode.max_time_sec - (time.perf_counter() - started_perf)) * 1000))
        subsequence_success_len = subtasks_solved
        wallclock_step_ms = int((time.perf_counter() - decision_started) * 1000)

        steps_rows.append(
            {
                "run_id": run_id,
                "episode_id": episode.episode_id,
                "step_idx": step_idx,
                "subtask_idx": min(subtask_idx, episode.subtasks_total),
                "timestamp_utc": _utc_now(),
                "sequence_id": episode.sequence_id,
                "condition_id": episode.condition_id,
                "scenario_profile_id": episode.scenario_profile_id,
                "action_level_id": episode.action_level_id,
                "observation_profile_id": episode.observation_profile_id,
                "pair_id": episode.pair_id,
                "current_instruction_text": current_instruction,
                "oracle_target_subtask": current_subtask,
                "decision_granularity": episode.decision_granularity,
                "active_modalities": _safe_json(protocol_obs["active_modalities"]),
                "sensor_mask_before": _safe_json(protocol_obs["sensor_mask_before"]),
                "sensor_mask_after": _safe_json(protocol_obs["sensor_mask_after"]),
                "model_input_summary": _safe_json(observation_summary),
                "model_output_raw": _safe_json(action_raw),
                "executor_applied_action": _safe_json(executor_applied_action),
                "model_status": model_status,
                "model_error_code": model_error_code,
                "model_error_message": model_error_message,
                "action_valid_flag": str(bool(action_valid)).lower(),
                "oracle_success_current_step": str(bool(success_current_step)).lower(),
                "subtask_status": subtask_status,
                "episode_status": episode_status,
                "subsequence_success_len": subsequence_success_len,
                "noise_applied_flag": str(bool(protocol_obs["noise_applied_flag"])).lower(),
                "safety_flag": str(bool(safety_flag)).lower(),
                "safety_reaction": safety_reaction,
                "rollback_attempted": str(bool(rollback_attempted)).lower(),
                "rollback_success": str(bool(rollback_success)).lower(),
                "recovery_phase": recovery_phase,
                "replan_event": str(bool(replan_event)).lower(),
                "decision_time_ms": int(decision_time * 1000),
                "predict_time_ms": int(predict_time * 1000),
                "executor_time_ms": int(executor_elapsed * 1000),
                "env_step_time_ms": int(env_step_elapsed * 1000),
                "oracle_check_time_ms": int(oracle_elapsed * 1000),
                "wallclock_step_time_ms": wallclock_step_ms,
                "budget_left_subtask_steps": budget_left_subtask,
                "budget_left_episode_steps": budget_left_episode,
                "budget_left_time_ms": budget_left_time_ms,
                "termination_reason": termination_reason_step,
                "notes": _safe_json(
                    {
                        "safety_intent": safety_intent,
                        "fallback_mode": fallback_mode,
                        "replan_requested": replan_requested,
                        "rollback_requested": rollback_requested,
                        "event_context": event_context,
                    }
                ),
            }
        )

        reaction_type = safety_reaction if safety_reaction != "none" else ("replan" if replan_event else "continue")
        events_rows.extend(
            _build_event_rows(
                run_id=run_id,
                episode=episode,
                step_idx=step_idx,
                subtask_idx=min(subtask_idx, episode.subtasks_total),
                active_events=active_events,
                noise_applied_flag=bool(protocol_obs["noise_applied_flag"]),
                reaction_type=reaction_type,
            )
        )

        if save_frames:
            frame, frame_channel = _select_frame(protocol_obs=protocol_obs, raw_obs=obs)
            if frame is not None:
                out_dir = frames_root / model_id / episode.episode_id
                ensure_dir(out_dir)
                frame_path, frame_hash = _save_frame(frame=frame, out_dir=out_dir, step_idx=step_idx)
                if frame_path is not None:
                    frames_rows.append(
                        {
                            "run_id": run_id,
                            "model_id": model_id,
                            "model_family": model_family,
                            "episode_id": episode.episode_id,
                            "condition_id": episode.condition_id,
                            "step_idx": step_idx,
                            "frame_mode": "rgb_array",
                            "camera_channel": frame_channel,
                            "frame_path": str(frame_path),
                            "frame_hash_md5": frame_hash,
                            "saved_at_utc": _utc_now(),
                        }
                    )

        if status != "running":
            break

    if status == "running":
        if steps_total >= episode.max_episode_steps:
            status = "failed"
            terminate_reason = "max_episode_steps_reached"
        else:
            status = "failed"
            terminate_reason = "unknown_termination"

    episodes_rows.append(
        {
            "run_id": run_id,
            "track": episode.track,
            "model_id": model_id,
            "model_family": model_family,
            "episode_id": episode.episode_id,
            "sequence_id": episode.sequence_id,
            "initial_state_id": episode.initial_state_id,
            "condition_id": episode.condition_id,
            "scenario_profile_id": episode.scenario_profile_id,
            "action_level_id": episode.action_level_id,
            "observation_profile_id": episode.observation_profile_id,
            "pair_id": episode.pair_id,
            "baseline_episode_id": episode.baseline_episode_id,
            "subtasks_total": episode.subtasks_total,
            "subtasks_solved": subtasks_solved,
            "max_subtask_steps": episode.max_subtask_steps,
            "max_episode_steps": episode.max_episode_steps,
            "max_time_sec": episode.max_time_sec,
            "status": status,
            "steps_total": steps_total,
            "terminate_reason": terminate_reason,
            "decision_time_total_sec": f"{decision_total:.6f}",
            "predict_time_total_sec": f"{predict_total:.6f}",
            "executor_time_total_sec": f"{executor_total:.6f}",
            "env_step_time_total_sec": f"{env_step_total:.6f}",
            "oracle_check_time_total_sec": f"{oracle_total:.6f}",
            "episode_wallclock_total_sec": f"{(time.perf_counter() - started_perf):.6f}",
            "started_at_utc": started_utc,
            "finished_at_utc": _utc_now(),
            "manifest_hash": manifest_hash,
        }
    )
    return WorkerOutput(
        episodes=episodes_rows,
        steps=steps_rows,
        events=events_rows,
        frames=frames_rows,
        errors=errors,
    )


def _write_run_readme(run_dir: Path) -> None:
    text = "\n".join(
        [
            "# Step3 Run Artifacts",
            "",
            "Quick read order:",
            "1. run_overview.json",
            "2. logs/episodes_index.csv",
            "3. logs/episodes_all.csv",
            "4. logs/steps_all.csv",
            "5. logs/events_all.csv",
            "6. frames_manifest.csv",
            "",
            "Notes:",
            "- cells/* keeps protocol-native split by condition_id.",
            "- logs/* is flat for quick analytics without joining per-cell folders.",
        ]
    )
    (run_dir / "README.md").write_text(text + "\n", encoding="utf-8")


def run_calvin_benchmark_step3(
    calvin_root: Path,
    dataset_path: Path,
    protocol_root: Path,
    contracts_root: Path,
    run_root: Path,
    model_id: str,
    model_family: str,
    model_backend: str,
    model_host: str,
    model_port: int,
    model_timeout_sec: float,
    python_model_spec: str | None,
    python_model_kwargs: Dict[str, Any] | None,
    parallel_workers: int,
    benchmark_size: int,
    save_frames: bool,
    max_episodes: int,
    allow_subtask_skip: bool,
    allow_incompatible_conditions: bool,
    show_gui: bool,
) -> Dict[str, Any]:
    if parallel_workers <= 0:
        raise ValueError("parallel_workers must be > 0")
    protocol_root = protocol_root.resolve()
    contracts_root = contracts_root.resolve()
    run_root = run_root.resolve()
    ensure_dir(run_root)

    contracts = load_protocol_contracts(protocol_root=protocol_root, contracts_root=contracts_root)
    all_episodes = list(contracts.episodes)
    episodes_planned_total = len(all_episodes)

    episodes = all_episodes
    limit = max_episodes if max_episodes > 0 else benchmark_size
    if limit > 0:
        episodes = episodes[:limit]
    episodes_selected_total = len(episodes)
    episodes_truncated = max(0, episodes_planned_total - episodes_selected_total)

    run_id = _build_run_id()
    run_dir = run_root / run_id
    ensure_dir(run_dir)
    ensure_dir(run_dir / "manifest")
    ensure_dir(run_dir / "env_setup")
    if save_frames:
        ensure_dir(run_dir / "frames")

    input_hashes = _copy_manifest_inputs(
        run_manifest_dir=run_dir / "manifest",
        protocol_root=protocol_root,
        contracts_root=contracts_root,
    )
    scenario_manifest = contracts.scenario_contract_manifest
    manifest_hash = str(scenario_manifest.get("run_signature", ""))

    by_worker = _split_by_worker(episodes=episodes, workers=parallel_workers)
    progress_lock = threading.Lock()
    completed_episodes = 0
    started_global = time.perf_counter()

    def progress_update() -> None:
        nonlocal completed_episodes
        with progress_lock:
            completed_episodes += 1
            total = max(1, episodes_selected_total)
            pct = (completed_episodes / total) * 100.0
            elapsed = time.perf_counter() - started_global
            print(f"[step3] progress {completed_episodes}/{total} ({pct:.1f}%) elapsed={elapsed:.1f}s")

    def worker(worker_id: int) -> WorkerOutput:
        adapter = build_model_adapter(
            backend=model_backend,
            model_host=model_host,
            model_port=model_port,
            model_timeout_sec=model_timeout_sec,
            python_model_spec=python_model_spec,
            python_model_kwargs=python_model_kwargs,
            seed=worker_id,
        )
        env = None
        task_oracle = None
        out = WorkerOutput([], [], [], [], [])
        try:
            env, task_oracle = _instantiate_env_and_oracle(
                calvin_root=calvin_root,
                dataset_path=dataset_path,
                show_gui=show_gui,
            )
            for episode in by_worker.get(worker_id, []):
                ep_out = _run_episode(
                    run_id=run_id,
                    model_id=model_id,
                    model_family=model_family,
                    episode=episode,
                    contracts=contracts,
                    model_adapter=adapter,
                    env=env,
                    task_oracle=task_oracle,
                    manifest_hash=manifest_hash,
                    save_frames=save_frames,
                    frames_root=run_dir / "frames",
                    model_backend=model_backend,
                    allow_subtask_skip=allow_subtask_skip,
                    allow_incompatible_conditions=allow_incompatible_conditions,
                )
                out.episodes.extend(ep_out.episodes)
                out.steps.extend(ep_out.steps)
                out.events.extend(ep_out.events)
                out.frames.extend(ep_out.frames)
                out.errors.extend(ep_out.errors)
                progress_update()
            return out
        except Exception as exc:
            out.errors.append(f"worker_{worker_id}:{exc}")
            return out
        finally:
            try:
                adapter.close()
            except Exception:
                pass
            if env is not None:
                try:
                    env.close()
                except Exception:
                    pass

    print(
        f"[step3] run_id={run_id} episodes={episodes_selected_total} "
        f"workers={parallel_workers} model={model_id} backend={model_backend}"
    )
    all_episode_rows: List[Dict[str, Any]] = []
    all_step_rows: List[Dict[str, Any]] = []
    all_event_rows: List[Dict[str, Any]] = []
    all_frame_rows: List[Dict[str, Any]] = []
    all_errors: List[str] = []
    worker_status: Dict[int, str] = {}
    worker_notes: Dict[int, str] = {}

    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        futures = {executor.submit(worker, worker_id): worker_id for worker_id in range(parallel_workers)}
        for future in as_completed(futures):
            worker_id = futures[future]
            assigned = len(by_worker.get(worker_id, []))
            try:
                result = future.result()
            except Exception as exc:
                err = f"worker_{worker_id}:{exc}"
                all_errors.append(err)
                worker_status[worker_id] = "failed"
                worker_notes[worker_id] = err
                continue
            all_episode_rows.extend(result.episodes)
            all_step_rows.extend(result.steps)
            all_event_rows.extend(result.events)
            all_frame_rows.extend(result.frames)
            all_errors.extend(result.errors)
            if assigned == 0:
                worker_status[worker_id] = "idle"
                worker_notes[worker_id] = "no_episodes_assigned"
            elif len(result.errors) > 0 and len(result.episodes) > 0:
                worker_status[worker_id] = "completed_with_errors"
                worker_notes[worker_id] = result.errors[0]
            elif len(result.errors) > 0:
                worker_status[worker_id] = "failed"
                worker_notes[worker_id] = result.errors[0]
            else:
                worker_status[worker_id] = "completed"
                worker_notes[worker_id] = f"episodes={assigned}"

    schema_refs = contracts.schema_refs
    episode_columns = list(schema_refs.get("episode_log_fields", []))
    step_columns = list(schema_refs.get("step_log_fields", []))
    event_columns = list(schema_refs.get("event_log_fields", []))

    cells_dir = write_condition_cells(
        run_dir=run_dir,
        episodes_rows=all_episode_rows,
        steps_rows=all_step_rows,
        events_rows=all_event_rows,
        episode_columns=episode_columns,
        step_columns=step_columns,
        event_columns=event_columns,
    )
    flat_logs = write_flat_logs(
        run_dir=run_dir,
        episodes_rows=all_episode_rows,
        steps_rows=all_step_rows,
        events_rows=all_event_rows,
        episode_columns=episode_columns,
        step_columns=step_columns,
        event_columns=event_columns,
    )
    episode_index_path = write_episode_index(
        run_dir=run_dir,
        model_id=model_id,
        episodes_rows=all_episode_rows,
        steps_rows=all_step_rows,
        events_rows=all_event_rows,
        frames_rows=all_frame_rows,
    )
    frames_manifest_path = write_frames_manifest(run_dir=run_dir, frames_rows=all_frame_rows)
    _write_run_readme(run_dir)

    env_registry_path = run_dir / "env_setup" / "env_registry.csv"
    with env_registry_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["run_id", "worker_id", "status", "note"],
        )
        writer.writeheader()
        for worker_id in range(parallel_workers):
            writer.writerow(
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "status": worker_status.get(worker_id, "unknown"),
                    "note": worker_notes.get(worker_id, ""),
                }
            )

    if len(all_errors) == 0:
        run_status = "completed"
    elif len(all_episode_rows) > 0:
        run_status = "completed_with_errors"
    else:
        run_status = "failed"

    run_summary = {
        "run_id": run_id,
        "status": run_status,
        "model_id": model_id,
        "model_family": model_family,
        "model_backend": model_backend,
        "parallel_workers": parallel_workers,
        "episodes_planned_total": episodes_planned_total,
        "episodes_selected_total": episodes_selected_total,
        "episodes_truncated_by_limit": episodes_truncated,
        "selection_policy": "stable_head",
        "counts_by_action_level": _counts_by_field(episodes, key=lambda x: x.action_level_id),
        "counts_by_scenario_profile": _counts_by_field(episodes, key=lambda x: x.scenario_profile_id),
        "counts_by_observation_profile": _counts_by_field(episodes, key=lambda x: x.observation_profile_id),
        "episodes_total": len(all_episode_rows),
        "steps_total": len(all_step_rows),
        "events_total": len(all_event_rows),
        "frames_total": len(all_frame_rows),
        "worker_errors_total": len(all_errors),
        "worker_errors_preview": all_errors[:20],
        "input_hashes": input_hashes,
        "output_paths": {
            "run_dir": str(run_dir),
            "env_registry_csv": str(env_registry_path),
            "cells_dir": str(cells_dir),
            "flat_logs_dir": str(flat_logs["logs_dir"]),
            "episodes_all_csv": str(flat_logs["episodes_all_csv"]),
            "steps_all_csv": str(flat_logs["steps_all_csv"]),
            "events_all_csv": str(flat_logs["events_all_csv"]),
            "episodes_index_csv": str(episode_index_path),
            "frames_manifest_csv": str(frames_manifest_path),
        },
        "config": {
            "dataset_path": str(dataset_path),
            "protocol_root": str(protocol_root),
            "contracts_root": str(contracts_root),
            "run_root": str(run_root),
            "benchmark_size": benchmark_size,
            "max_episodes": max_episodes,
            "save_frames": bool(save_frames),
            "allow_subtask_skip": bool(allow_subtask_skip),
            "allow_incompatible_conditions": bool(allow_incompatible_conditions),
            "show_gui": bool(show_gui),
            "model_host": model_host,
            "model_port": model_port,
            "model_timeout_sec": model_timeout_sec,
            "python_model_spec": python_model_spec or "",
        },
        "schemas": {
            "episode_columns": episode_columns,
            "step_columns": step_columns,
            "event_columns": event_columns,
        },
    }
    run_overview = {
        "run_id": run_id,
        "status": run_status,
        "model_id": model_id,
        "model_family": model_family,
        "model_backend": model_backend,
        "parallel_workers": parallel_workers,
        "selection": {
            "episodes_planned_total": episodes_planned_total,
            "episodes_selected_total": episodes_selected_total,
            "episodes_truncated_by_limit": episodes_truncated,
            "benchmark_size": benchmark_size,
            "max_episodes": max_episodes,
        },
        "result_counts": {
            "episodes_total": len(all_episode_rows),
            "steps_total": len(all_step_rows),
            "events_total": len(all_event_rows),
            "frames_total": len(all_frame_rows),
            "worker_errors_total": len(all_errors),
        },
        "artifacts": run_summary["output_paths"],
        "quick_read_order": [
            "run_overview.json",
            "logs/episodes_index.csv",
            "logs/episodes_all.csv",
            "logs/steps_all.csv",
            "logs/events_all.csv",
            "frames_manifest.csv",
        ],
    }
    (run_dir / "run_summary.json").write_text(json.dumps(run_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "run_overview.json").write_text(json.dumps(run_overview, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_summary
