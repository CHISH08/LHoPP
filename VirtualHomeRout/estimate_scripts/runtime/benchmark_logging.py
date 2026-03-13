import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


EPISODE_LOG_COLUMNS = [
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

STEP_LOG_COLUMNS = [
    "run_id",
    "model_id",
    "family",
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

EVENT_LOG_COLUMNS = [
    "run_id",
    "model_id",
    "family",
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

ENV_REGISTRY_COLUMNS = [
    "run_id",
    "slot_id",
    "worker_id",
    "port",
    "pid",
    "status",
    "startup_time_sec",
    "error",
]

FRAMES_MANIFEST_COLUMNS = [
    "run_id",
    "model_id",
    "family",
    "episode_id",
    "condition_id",
    "step_idx",
    "frame_mode",
    "camera_index",
    "frame_path",
    "frame_source",
    "action_exec",
    "sim_success_flag",
    "frame_hash_md5",
    "sim_exec_time_step_sec",
    "episode_wallclock_step_sec",
    "saved_at_utc",
]

EPISODE_INDEX_COLUMNS = [
    "run_id",
    "episode_id",
    "stratum",
    "condition_id",
    "scenario_level",
    "scenario_variant",
    "status",
    "terminate_reason",
    "steps_logged",
    "events_logged",
    "frames_saved",
    "cell_dir",
    "frame_dir",
]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_env_registry(run_dir: Path, rows: List[Dict[str, Any]]) -> Path:
    out = run_dir / "env_setup" / "env_registry.csv"
    write_csv(out, ENV_REGISTRY_COLUMNS, rows)
    return out


def write_frames_manifest(run_dir: Path, rows: List[Dict[str, Any]]) -> Path:
    out = run_dir / "frames_manifest.csv"
    ordered = sorted(rows, key=lambda x: (str(x["episode_id"]), int(x["step_idx"])))
    write_csv(out, FRAMES_MANIFEST_COLUMNS, ordered)
    return out


def write_condition_cells(
    run_dir: Path,
    episodes_rows: List[Dict[str, Any]],
    steps_rows: List[Dict[str, Any]],
    events_rows: List[Dict[str, Any]],
) -> Path:
    cells_dir = run_dir / "cells"
    ensure_dir(cells_dir)
    by_episode_condition = {str(row["episode_id"]): str(row["condition_id"]) for row in episodes_rows}
    all_conditions = sorted(set(row["condition_id"] for row in episodes_rows))

    for condition_id in all_conditions:
        cell_dir = cells_dir / condition_id
        ensure_dir(cell_dir)
        condition_episodes = sorted(
            [row for row in episodes_rows if row["condition_id"] == condition_id],
            key=lambda x: str(x["episode_id"]),
        )
        condition_steps = sorted(
            [row for row in steps_rows if by_episode_condition.get(str(row["episode_id"])) == condition_id],
            key=lambda x: (str(x["episode_id"]), int(x["step_idx"])),
        )
        condition_events = sorted(
            [row for row in events_rows if by_episode_condition.get(str(row["episode_id"])) == condition_id],
            key=lambda x: (str(x["episode_id"]), int(x["step_idx"]), str(x["event_id"])),
        )
        write_csv(cell_dir / "episodes.csv", EPISODE_LOG_COLUMNS, condition_episodes)
        write_csv(cell_dir / "steps.csv", STEP_LOG_COLUMNS, condition_steps)
        write_csv(cell_dir / "events.csv", EVENT_LOG_COLUMNS, condition_events)
        metadata = {
            "condition_id": condition_id,
            "episodes_count": len(condition_episodes),
            "steps_count": len(condition_steps),
            "events_count": len(condition_events),
        }
        (cell_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return cells_dir


def write_flat_logs(
    run_dir: Path,
    episodes_rows: List[Dict[str, Any]],
    steps_rows: List[Dict[str, Any]],
    events_rows: List[Dict[str, Any]],
) -> Dict[str, Path]:
    logs_dir = run_dir / "logs"
    ensure_dir(logs_dir)
    episodes_sorted = sorted(episodes_rows, key=lambda x: str(x["episode_id"]))
    steps_sorted = sorted(steps_rows, key=lambda x: (str(x["episode_id"]), int(x["step_idx"])))
    events_sorted = sorted(events_rows, key=lambda x: (str(x["episode_id"]), int(x["step_idx"]), str(x["event_id"])))
    episodes_csv = logs_dir / "episodes_all.csv"
    steps_csv = logs_dir / "steps_all.csv"
    events_csv = logs_dir / "events_all.csv"
    write_csv(episodes_csv, EPISODE_LOG_COLUMNS, episodes_sorted)
    write_csv(steps_csv, STEP_LOG_COLUMNS, steps_sorted)
    write_csv(events_csv, EVENT_LOG_COLUMNS, events_sorted)
    return {
        "logs_dir": logs_dir,
        "episodes_all_csv": episodes_csv,
        "steps_all_csv": steps_csv,
        "events_all_csv": events_csv,
    }


def write_episode_index(
    run_dir: Path,
    model_id: str,
    episodes_rows: List[Dict[str, Any]],
    steps_rows: List[Dict[str, Any]],
    events_rows: List[Dict[str, Any]],
    frames_rows: List[Dict[str, Any]],
) -> Path:
    steps_by_episode = defaultdict(int)
    events_by_episode = defaultdict(int)
    frames_by_episode = defaultdict(int)
    for row in steps_rows:
        steps_by_episode[str(row["episode_id"])] += 1
    for row in events_rows:
        events_by_episode[str(row["episode_id"])] += 1
    for row in frames_rows:
        frames_by_episode[str(row["episode_id"])] += 1

    rows: List[Dict[str, Any]] = []
    for episode in sorted(episodes_rows, key=lambda x: str(x["episode_id"])):
        episode_id = str(episode["episode_id"])
        rows.append(
            {
                "run_id": str(episode["run_id"]),
                "episode_id": episode_id,
                "stratum": str(episode["stratum"]),
                "condition_id": str(episode["condition_id"]),
                "scenario_level": str(episode["scenario_level"]),
                "scenario_variant": str(episode["scenario_variant"]),
                "status": str(episode["status"]),
                "terminate_reason": str(episode["terminate_reason"]),
                "steps_logged": steps_by_episode.get(episode_id, 0),
                "events_logged": events_by_episode.get(episode_id, 0),
                "frames_saved": frames_by_episode.get(episode_id, 0),
                "cell_dir": str(run_dir / "cells" / str(episode["condition_id"])),
                "frame_dir": str(run_dir / "frames" / model_id / episode_id),
            }
        )

    out = run_dir / "logs" / "episodes_index.csv"
    write_csv(out, EPISODE_INDEX_COLUMNS, rows)
    return out
