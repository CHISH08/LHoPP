import csv
import hashlib
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import cv2

from unity_scripts.env_pool import EnvSlot, UnityEnvPool

from .benchmark_logging import (
    EPISODE_LOG_COLUMNS,
    EVENT_LOG_COLUMNS,
    STEP_LOG_COLUMNS,
    ensure_dir,
    write_episode_index,
    write_condition_cells,
    write_env_registry,
    write_flat_logs,
    write_frames_manifest,
)
from .env_runtime import (
    EventSpec,
    active_events_at_step,
    bootstrap_slot,
    build_action_mask,
    build_action_pool,
    choose_injected_action,
    collect_debug_frame,
    collect_graph,
    collect_observation,
    execute_action,
    normalize_action,
    parse_task_file,
    validate_action,
)
from .model_http_adapter import ModelHTTPAdapter, ModelHTTPConfig


@dataclass(frozen=True)
class EpisodeContract:
    episode_id: str
    pair_id: str
    task_id: str
    stratum: str
    condition_id: str
    condition_id_seeded: str
    scenario_level: str
    scenario_variant: str
    scenario_tag: str
    seed: int
    track: str
    reference_actions_count: int
    max_steps: int
    max_time_sec: int
    active_modalities: List[str]
    observation_bundle_policy: str


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
    return f"vh_step4_{stamp}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _load_episodes(contracts_root: Path) -> List[EpisodeContract]:
    rows = _read_csv(contracts_root / "episodes_contracts.csv")
    episodes: List[EpisodeContract] = []
    for row in rows:
        episodes.append(
            EpisodeContract(
                episode_id=row["episode_id"],
                pair_id=row["pair_id"],
                task_id=row["task_id"],
                stratum=row["stratum"],
                condition_id=row["condition_id"],
                condition_id_seeded=row["condition_id_seeded"],
                scenario_level=row["scenario_level"],
                scenario_variant=row["scenario_variant"],
                scenario_tag=row["scenario_tag"],
                seed=int(row["seed"]),
                track=row["track"],
                reference_actions_count=int(row["reference_actions_count"]),
                max_steps=int(row["max_steps"]),
                max_time_sec=int(row["max_time_sec"]),
                active_modalities=json.loads(row["active_modalities_json"]),
                observation_bundle_policy=row["observation_bundle_policy"],
            )
        )
    return sorted(episodes, key=lambda x: x.episode_id)


def _load_events(contracts_root: Path) -> Dict[str, List[EventSpec]]:
    rows = _read_csv(contracts_root / "events_schedule.csv")
    out: Dict[str, List[EventSpec]] = {}
    for row in rows:
        spec = EventSpec(
            episode_id=row["episode_id"],
            event_id=row["event_id"],
            event_type=row["event_type"],
            event_source=row["event_source"],
            start_step=int(row["start_step"]),
            end_step=int(row["end_step"]),
            payload=json.loads(row["event_payload_json"]),
        )
        out.setdefault(spec.episode_id, []).append(spec)
    for episode_id in out:
        out[episode_id] = sorted(out[episode_id], key=lambda x: (x.start_step, x.end_step, x.event_id))
    return out


def _load_tasks(tasks_root: Path, episodes: List[EpisodeContract]):
    tasks = {}
    for task_id in sorted(set(ep.task_id for ep in episodes)):
        task_path = tasks_root / task_id
        if not task_path.exists():
            raise FileNotFoundError(f"Task is missing in bundle: {task_path}")
        task = parse_task_file(task_id=task_id, task_path=task_path)
        if not task.actions:
            raise RuntimeError(f"Task has no executable actions: {task_path}")
        tasks[task_id] = task
    return tasks


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


def _split_by_slot(episodes: List[EpisodeContract], workers: int) -> Dict[int, List[EpisodeContract]]:
    out: Dict[int, List[EpisodeContract]] = {i: [] for i in range(workers)}
    for idx, episode in enumerate(episodes):
        out[idx % workers].append(episode)
    return out


def _counts_by_stratum(episodes: List[EpisodeContract]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for ep in episodes:
        out[ep.stratum] = out.get(ep.stratum, 0) + 1
    return dict(sorted(out.items(), key=lambda x: x[0]))


def _extract_model_action(model_resp: Dict[str, Any]) -> str:
    action_exec = str(model_resp.get("action_exec", "") or "").strip()
    action_raw = str(model_resp.get("action_raw", "") or "").strip()
    return action_exec if action_exec else action_raw


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _save_frame(
    frame,
    frames_root: Path,
    model_id: str,
    episode_id: str,
    step_idx: int,
) -> Tuple[Path | None, str]:
    if frame is None:
        return None, ""
    frame_hash_md5 = hashlib.md5(frame.tobytes()).hexdigest()
    out_dir = frames_root / model_id / episode_id
    ensure_dir(out_dir)
    out_path = out_dir / f"step_{step_idx:04d}.png"
    if cv2.imwrite(str(out_path), frame):
        return out_path, frame_hash_md5
    return None, ""


def _is_recoverable_observation_error(message: str) -> bool:
    text = (message or "").lower()
    return ("driver is null" in text) or ("read timed out" in text) or ("timeout" in text)


def _run_episode(
    run_id: str,
    slot: EnvSlot,
    episode: EpisodeContract,
    task,
    episode_events: List[EventSpec],
    model_adapter: ModelHTTPAdapter,
    model_id: str,
    model_family: str,
    frames_root: Path,
    save_frames: bool,
    frame_camera_index: int,
    frame_mode: str,
    image_width: int,
    image_height: int,
    time_scale: float,
    skip_animation: bool,
) -> WorkerOutput:
    episodes_rows: List[Dict[str, Any]] = []
    steps_rows: List[Dict[str, Any]] = []
    events_rows: List[Dict[str, Any]] = []
    frames_rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    if slot.comm is None:
        errors.append(f"{episode.episode_id}: slot comm not initialized")
        return WorkerOutput(episodes_rows, steps_rows, events_rows, frames_rows, errors)

    started_utc = _utc_now()
    started_perf = time.perf_counter()
    history_actions: List[str] = []
    history_events: List[str] = []
    decision_total = 0.0
    sim_total = 0.0
    steps_total = 0
    steps_by_model = 0
    status = "ok"
    terminate_reason = ""

    ok_boot, boot_err = bootstrap_slot(slot, task.scene_idx)
    if not ok_boot:
        status = "invalid"
        terminate_reason = f"env_init_failed:{boot_err}"
    else:
        ok_graph, graph, graph_err = collect_graph(slot.comm)
        if not ok_graph:
            status = "invalid"
            terminate_reason = f"graph_unavailable:{graph_err}"
            graph = {}
        action_pool = build_action_pool(task.actions, graph)
        reference_norm = [normalize_action(x) for x in task.actions]
        progress_idx = 0

        for step_idx in range(1, episode.max_steps + 1):
            if (time.perf_counter() - started_perf) > episode.max_time_sec:
                terminate_reason = "max_time_reached"
                break

            active_events = active_events_at_step(episode_events, step_idx)
            observation_bundle, active_modalities, frame, obs_err = collect_observation(
                comm=slot.comm,
                episode_id=episode.episode_id,
                condition_id=episode.condition_id,
                active_modalities=episode.active_modalities,
                active_events=active_events,
                step_idx=step_idx,
                frame_camera_index=frame_camera_index,
                frame_mode=frame_mode,
                image_width=image_width,
                image_height=image_height,
                history_events=history_events,
            )
            if obs_err:
                recovered = False
                if _is_recoverable_observation_error(obs_err):
                    ok_recover, recover_err = bootstrap_slot(slot, task.scene_idx)
                    if ok_recover:
                        ok_graph_recover, graph_recover, _ = collect_graph(slot.comm)
                        if ok_graph_recover:
                            action_pool = build_action_pool(task.actions, graph_recover)
                        observation_bundle, active_modalities, frame, obs_err_retry = collect_observation(
                            comm=slot.comm,
                            episode_id=episode.episode_id,
                            condition_id=episode.condition_id,
                            active_modalities=episode.active_modalities,
                            active_events=active_events,
                            step_idx=step_idx,
                            frame_camera_index=frame_camera_index,
                            frame_mode=frame_mode,
                            image_width=image_width,
                            image_height=image_height,
                            history_events=history_events,
                        )
                        if not obs_err_retry:
                            recovered = True
                            history_events.append("runtime:observation_recover_reset")
                            obs_err = ""
                        else:
                            obs_err = f"{obs_err}; retry_failed:{obs_err_retry}"
                    else:
                        obs_err = f"{obs_err}; recover_failed:{recover_err}"
                if not recovered:
                    status = "invalid"
                    terminate_reason = f"observation_failed:{obs_err}"
                    errors.append(f"{episode.episode_id}: {terminate_reason}")
                    break

            allowed_mask, mask_total = build_action_mask(
                scenario_level=episode.scenario_level,
                action_pool=action_pool,
                progress_idx=progress_idx,
                active_events=active_events,
            )

            payload = {
                "request_id": f"{run_id}:{episode.episode_id}:{step_idx}",
                "run_id": run_id,
                "model_id": model_id,
                "episode_id": episode.episode_id,
                "step_idx": step_idx,
                "task_instruction": task.title,
                "history_actions": history_actions[-20:],
                "history_events": history_events[-20:],
                "available_actions_mask": allowed_mask,
                "active_modalities": active_modalities,
                "observation_bundle": observation_bundle,
                "budget_left_steps": max(0, episode.max_steps - step_idx + 1),
                "budget_left_time_sec": max(0.0, episode.max_time_sec - (time.perf_counter() - started_perf)),
                "worker_slot": slot.slot_id,
            }

            pred_started = time.perf_counter()
            model_resp = model_adapter.predict(payload)
            decision_step = time.perf_counter() - pred_started
            if isinstance(model_resp.get("model_latency_sec"), (int, float)) and model_resp["model_latency_sec"] > 0:
                decision_step = float(model_resp["model_latency_sec"])
            decision_total += decision_step

            model_status = str(model_resp.get("status", "error"))
            action_raw = str(model_resp.get("action_raw", ""))
            action_exec = _extract_model_action(model_resp)
            error_code = str(model_resp.get("error_code", ""))
            error_message = str(model_resp.get("error_message", ""))

            if model_status == "unsupported":
                status = "unsupported"
                terminate_reason = "model_unsupported"
            elif model_status not in ("ok", "unsupported", "error"):
                model_status = "error"
                error_code = error_code or "runtime_exception"
                error_message = error_message or "unknown_model_status"

            valid = False
            if model_status == "ok":
                valid, v_err = validate_action(action_exec, allowed_mask)
                if not valid:
                    model_status = "error"
                    error_code = v_err
                    error_message = v_err

            sim_success = False
            sim_message: Any = ""
            sim_step = 0.0
            if model_status == "ok" and valid:
                steps_by_model += 1
                sim_success, sim_message, sim_step = execute_action(
                    slot.comm,
                    action_exec=action_exec,
                    time_scale=time_scale,
                    skip_animation=skip_animation,
                )

            injected_action = ""
            for event in active_events:
                before = {
                    "status": model_status,
                    "action_raw": action_raw,
                    "action_exec": action_exec,
                    "error_code": error_code,
                }
                after = dict(before)
                if event.event_type == "injected_random_action":
                    injected_action = choose_injected_action(
                        allowed_mask=allowed_mask,
                        action_pool=action_pool,
                        model_action_exec=action_exec,
                        episode_id=episode.episode_id,
                        step_idx=step_idx,
                        event_id=event.event_id,
                    )
                    if injected_action:
                        inj_ok, inj_msg, inj_sec = execute_action(
                            slot.comm,
                            action_exec=injected_action,
                            time_scale=time_scale,
                            skip_animation=skip_animation,
                        )
                        sim_step += inj_sec
                        after["injected_action"] = injected_action
                        after["injected_success"] = inj_ok
                        after["injected_message"] = inj_msg
                history_events.append(f"{event.event_id}:{event.event_type}")
                events_rows.append(
                    {
                        "run_id": run_id,
                        "model_id": model_id,
                        "family": model_family,
                        "episode_id": episode.episode_id,
                        "event_id": event.event_id,
                        "step_idx": step_idx,
                        "timestamp_utc": _utc_now(),
                        "scenario_level": episode.scenario_level,
                        "scenario_variant": episode.scenario_variant,
                        "event_type": event.event_type,
                        "event_source": event.event_source,
                        "event_payload_json": _safe_json(event.payload),
                        "model_response_before_event": _safe_json(before),
                        "model_response_after_event": _safe_json(after),
                        "resolved_flag": "true",
                        "resolve_step_idx": step_idx,
                        "resolve_latency_steps": 0,
                        "safety_reaction": "continue",
                    }
                )

            sim_total += sim_step
            if model_status == "ok" and valid and sim_success:
                history_actions.append(action_exec)
            elif model_status != "ok":
                history_actions.append(f"MODEL_ERROR:{error_code}")
            else:
                history_actions.append(f"SIM_FAIL:{action_exec}")

            if model_status == "ok" and sim_success and progress_idx < len(reference_norm):
                if normalize_action(action_exec) == reference_norm[progress_idx]:
                    progress_idx += 1

            wallclock_step = time.perf_counter() - started_perf
            steps_total += 1

            steps_rows.append(
                {
                    "run_id": run_id,
                    "model_id": model_id,
                    "family": model_family,
                    "episode_id": episode.episode_id,
                    "step_idx": step_idx,
                    "timestamp_utc": _utc_now(),
                    "scenario_level": episode.scenario_level,
                    "scenario_variant": episode.scenario_variant,
                    "scenario_tag": episode.scenario_tag,
                    "active_modalities": _safe_json(active_modalities),
                    "mask_size_total": mask_total,
                    "mask_size_allowed": len(allowed_mask),
                    "action_raw": action_raw,
                    "action_exec": action_exec,
                    "model_status": model_status,
                    "model_error_code": error_code,
                    "model_error_message": error_message,
                    "sim_success_flag": str(bool(sim_success)).lower(),
                    "sim_message": _safe_json(sim_message),
                    "decision_time_step_sec": f"{decision_step:.6f}",
                    "sim_exec_time_step_sec": f"{sim_step:.6f}",
                    "episode_wallclock_step_sec": f"{wallclock_step:.6f}",
                    "history_size": len(history_actions),
                    "plan_revision_id": "",
                    "safety_flag": str(bool(injected_action)).lower(),
                    "notes": _safe_json(
                        {
                            "condition_id_seeded": episode.condition_id_seeded,
                            "injected_action_exec": injected_action,
                            "time_scale": time_scale,
                        }
                    ),
                }
            )

            if save_frames:
                frame_source = "observation_bundle"
                if frame is None:
                    frame_source = "debug_env_fallback"
                    frame, _ = collect_debug_frame(
                        comm=slot.comm,
                        frame_camera_index=frame_camera_index,
                        frame_mode=frame_mode,
                        image_width=image_width,
                        image_height=image_height,
                    )
                if frame is None:
                    frame_source = "unavailable"
                frame_path, frame_hash_md5 = _save_frame(
                    frame=frame,
                    frames_root=frames_root,
                    model_id=model_id,
                    episode_id=episode.episode_id,
                    step_idx=step_idx,
                )
                if frame_path is not None:
                    frames_rows.append(
                        {
                            "run_id": run_id,
                            "model_id": model_id,
                            "family": model_family,
                            "episode_id": episode.episode_id,
                            "condition_id": episode.condition_id,
                            "step_idx": step_idx,
                            "frame_mode": frame_mode,
                            "camera_index": frame_camera_index,
                            "frame_path": str(frame_path),
                            "frame_source": frame_source,
                            "action_exec": action_exec,
                            "sim_success_flag": str(bool(sim_success)).lower(),
                            "frame_hash_md5": frame_hash_md5,
                            "sim_exec_time_step_sec": f"{sim_step:.6f}",
                            "episode_wallclock_step_sec": f"{wallclock_step:.6f}",
                            "saved_at_utc": _utc_now(),
                        }
                    )

            if progress_idx >= episode.reference_actions_count:
                terminate_reason = "success_reference_sequence"
                break
            if steps_by_model > (5 * episode.reference_actions_count):
                terminate_reason = "over_5x_reference"
                break
            if status in ("unsupported", "error", "invalid"):
                if not terminate_reason:
                    terminate_reason = f"model_status_{status}"
                break

    if not terminate_reason:
        if steps_total >= episode.max_steps:
            terminate_reason = "max_steps_reached"
        else:
            terminate_reason = "finished"

    episodes_rows.append(
        {
            "run_id": run_id,
            "track": episode.track,
            "model_id": model_id,
            "family": model_family,
            "episode_id": episode.episode_id,
            "task_id": episode.task_id,
            "task_title": task.title,
            "stratum": episode.stratum,
            "seed": episode.seed,
            "pair_id": episode.pair_id,
            "condition_id": episode.condition_id,
            "scenario_level": episode.scenario_level,
            "scenario_variant": episode.scenario_variant,
            "scenario_tag": episode.scenario_tag,
            "status": status,
            "max_steps": episode.max_steps,
            "max_time_sec": episode.max_time_sec,
            "steps_total": steps_total,
            "terminate_reason": terminate_reason,
            "decision_time_total_sec": f"{decision_total:.6f}",
            "sim_exec_time_total_sec": f"{sim_total:.6f}",
            "episode_wallclock_total_sec": f"{time.perf_counter() - started_perf:.6f}",
            "started_at_utc": started_utc,
            "finished_at_utc": _utc_now(),
        }
    )
    return WorkerOutput(episodes=episodes_rows, steps=steps_rows, events=events_rows, frames=frames_rows, errors=errors)


def _build_registry_rows(run_id: str, slots: List[EnvSlot]) -> List[Dict[str, Any]]:
    rows = []
    for slot in slots:
        rows.append(
            {
                "run_id": run_id,
                "slot_id": slot.slot_id,
                "worker_id": slot.worker_id,
                "port": slot.port,
                "pid": slot.pid if slot.pid is not None else "",
                "status": slot.status,
                "startup_time_sec": f"{slot.startup_time_sec:.6f}" if slot.startup_time_sec is not None else "",
                "error": slot.error,
            }
        )
    return rows


def run_unity_benchmark_step4(
    unity_exe: Path,
    run_root: Path,
    parallel_workers: int,
    base_port: int,
    time_scale: float,
    skip_animation: bool,
    image_width: int,
    image_height: int,
    model_id: str,
    model_family: str,
    model_host: str,
    model_port: int,
    model_timeout_sec: float,
    protocol_root: Path,
    contracts_root: Path,
    tasks_root: Path,
    save_frames: bool,
    frame_camera_index: int,
    frame_mode: str,
    video_fps: int,
    max_episodes: int,
) -> Dict[str, Any]:
    del video_fps
    run_id = _build_run_id()
    run_dir = run_root.resolve() / run_id
    ensure_dir(run_dir)
    ensure_dir(run_dir / "env_setup")
    ensure_dir(run_dir / "manifest")

    required = [
        protocol_root / "manifest" / "task_manifest.json",
        protocol_root / "manifest" / "benchmark_manifest.json",
        contracts_root / "episodes_contracts.csv",
        contracts_root / "events_schedule.csv",
        contracts_root / "scenario_contract_manifest.json",
        tasks_root,
    ]
    for path in required:
        if not Path(path).exists():
            raise FileNotFoundError(f"Required preflight artifact missing: {path}")

    input_hashes = _copy_manifest_inputs(
        run_manifest_dir=run_dir / "manifest",
        protocol_root=protocol_root,
        contracts_root=contracts_root,
    )

    all_episode_contracts = _load_episodes(contracts_root=contracts_root)
    episodes_planned_total = len(all_episode_contracts)
    episodes = all_episode_contracts
    if max_episodes > 0:
        episodes = all_episode_contracts[:max_episodes]
    episodes_selected_total = len(episodes)
    episodes_truncated = max(0, episodes_planned_total - episodes_selected_total)
    strata_planned_counts = _counts_by_stratum(all_episode_contracts)
    strata_selected_counts = _counts_by_stratum(episodes)
    events_map = _load_events(contracts_root=contracts_root)
    tasks = _load_tasks(tasks_root=tasks_root, episodes=episodes)
    by_slot = _split_by_slot(episodes=episodes, workers=parallel_workers)

    pool = UnityEnvPool(
        unity_exe=unity_exe,
        parallel_workers=parallel_workers,
        base_port=base_port,
    )
    all_episodes: List[Dict[str, Any]] = []
    all_steps: List[Dict[str, Any]] = []
    all_events: List[Dict[str, Any]] = []
    all_frames: List[Dict[str, Any]] = []
    all_errors: List[str] = []

    try:
        print(
            f"[step4] run_id={run_id} workers={parallel_workers} "
            f"model={model_id}@{model_host}:{model_port}"
        )
        slots = pool.start_all()
        env_registry_path = write_env_registry(run_dir=run_dir, rows=_build_registry_rows(run_id, slots))
        not_ready = [slot.slot_id for slot in slots if slot.status != "ready"]
        if not_ready:
            raise RuntimeError(f"Unity slots not ready: {not_ready}")

        def worker(slot: EnvSlot) -> WorkerOutput:
            adapter = ModelHTTPAdapter(
                ModelHTTPConfig(host=model_host, port=model_port, timeout_sec=model_timeout_sec)
            )
            out = WorkerOutput([], [], [], [], [])
            try:
                for episode in by_slot.get(slot.slot_id, []):
                    ep_out = _run_episode(
                        run_id=run_id,
                        slot=slot,
                        episode=episode,
                        task=tasks[episode.task_id],
                        episode_events=events_map.get(episode.episode_id, []),
                        model_adapter=adapter,
                        model_id=model_id,
                        model_family=model_family,
                        frames_root=run_dir / "frames",
                        save_frames=save_frames,
                        frame_camera_index=frame_camera_index,
                        frame_mode=frame_mode,
                        image_width=image_width,
                        image_height=image_height,
                        time_scale=time_scale,
                        skip_animation=skip_animation,
                    )
                    out.episodes.extend(ep_out.episodes)
                    out.steps.extend(ep_out.steps)
                    out.events.extend(ep_out.events)
                    out.frames.extend(ep_out.frames)
                    out.errors.extend(ep_out.errors)
                return out
            finally:
                adapter.close()

        with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
            futures = [executor.submit(worker, slot) for slot in slots]
            for future in as_completed(futures):
                data = future.result()
                all_episodes.extend(data.episodes)
                all_steps.extend(data.steps)
                all_events.extend(data.events)
                all_frames.extend(data.frames)
                all_errors.extend(data.errors)

        cells_dir = write_condition_cells(
            run_dir=run_dir,
            episodes_rows=all_episodes,
            steps_rows=all_steps,
            events_rows=all_events,
        )
        flat_logs = write_flat_logs(
            run_dir=run_dir,
            episodes_rows=all_episodes,
            steps_rows=all_steps,
            events_rows=all_events,
        )
        episodes_index_path = write_episode_index(
            run_dir=run_dir,
            model_id=model_id,
            episodes_rows=all_episodes,
            steps_rows=all_steps,
            events_rows=all_events,
            frames_rows=all_frames,
        )
        frames_manifest_path = write_frames_manifest(run_dir=run_dir, rows=all_frames)

        summary = {
            "run_id": run_id,
            "status": "completed",
            "model_id": model_id,
            "family": model_family,
            "parallel_workers": parallel_workers,
            "episodes_planned_total": episodes_planned_total,
            "episodes_selected_total": episodes_selected_total,
            "episodes_truncated_by_max_episodes": episodes_truncated,
            "selection_policy": "stable_sort_by_episode_id_then_head",
            "strata_planned_counts": strata_planned_counts,
            "strata_selected_counts": strata_selected_counts,
            "episodes_total": len(all_episodes),
            "steps_total": len(all_steps),
            "events_total": len(all_events),
            "frames_total": len(all_frames),
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
                "episodes_index_csv": str(episodes_index_path),
                "frames_manifest_csv": str(frames_manifest_path),
            },
            "config": {
                "strict_blind": True,
                "base_port": base_port,
                "time_scale": time_scale,
                "skip_animation": bool(skip_animation),
                "image_width": image_width,
                "image_height": image_height,
                "save_frames": bool(save_frames),
                "frame_camera_index": frame_camera_index,
                "frame_mode": frame_mode,
                "model_host": model_host,
                "model_port": model_port,
                "model_timeout_sec": model_timeout_sec,
                "max_episodes": max_episodes,
            },
            "schemas": {
                "episode_columns": EPISODE_LOG_COLUMNS,
                "step_columns": STEP_LOG_COLUMNS,
                "event_columns": EVENT_LOG_COLUMNS,
            },
        }
        run_overview = {
            "run_id": run_id,
            "model_id": model_id,
            "family": model_family,
            "parallel_workers": parallel_workers,
            "selection": {
                "episodes_planned_total": episodes_planned_total,
                "episodes_selected_total": episodes_selected_total,
                "episodes_truncated_by_max_episodes": episodes_truncated,
                "max_episodes": max_episodes,
                "strata_planned_counts": strata_planned_counts,
                "strata_selected_counts": strata_selected_counts,
            },
            "result_counts": {
                "episodes_total": len(all_episodes),
                "steps_total": len(all_steps),
                "events_total": len(all_events),
                "frames_total": len(all_frames),
                "worker_errors_total": len(all_errors),
            },
            "artifacts": summary["output_paths"],
            "quick_read_order": [
                "run_overview.json",
                "logs/episodes_index.csv",
                "logs/episodes_all.csv",
                "logs/steps_all.csv",
                "logs/events_all.csv",
                "frames_manifest.csv",
            ],
        }
        (run_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "run_overview.json").write_text(
            json.dumps(run_overview, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "README.md").write_text(
            "\n".join(
                [
                    "# Step4 Run Artifacts",
                    "",
                    "Быстрый просмотр:",
                    "1. run_overview.json",
                    "2. logs/episodes_index.csv",
                    "3. logs/episodes_all.csv",
                    "4. logs/steps_all.csv",
                    "5. logs/events_all.csv",
                    "6. frames_manifest.csv",
                    "",
                    "Пояснение:",
                    "- cells/* оставлены для протокольной структуры по condition_id.",
                    "- logs/* добавлены как плоский слой для быстрого анализа без склейки по папкам.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return summary
    finally:
        pool.close_all()
