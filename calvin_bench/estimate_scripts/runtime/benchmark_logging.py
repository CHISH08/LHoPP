import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List


EPISODE_INDEX_COLUMNS = [
    "run_id",
    "episode_id",
    "sequence_id",
    "condition_id",
    "action_level_id",
    "scenario_profile_id",
    "observation_profile_id",
    "status",
    "terminate_reason",
    "steps_logged",
    "events_logged",
    "frames_saved",
    "cell_dir",
    "frame_dir",
]

FRAMES_MANIFEST_COLUMNS = [
    "run_id",
    "model_id",
    "model_family",
    "episode_id",
    "condition_id",
    "step_idx",
    "frame_mode",
    "camera_channel",
    "frame_path",
    "frame_hash_md5",
    "saved_at_utc",
]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, fieldnames: List[str], rows: Iterable[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_flat_logs(
    run_dir: Path,
    episodes_rows: List[Dict[str, Any]],
    steps_rows: List[Dict[str, Any]],
    events_rows: List[Dict[str, Any]],
    episode_columns: List[str],
    step_columns: List[str],
    event_columns: List[str],
) -> Dict[str, Path]:
    logs_dir = run_dir / "logs"
    ensure_dir(logs_dir)
    episodes_all = logs_dir / "episodes_all.csv"
    steps_all = logs_dir / "steps_all.csv"
    events_all = logs_dir / "events_all.csv"
    write_csv(
        episodes_all,
        episode_columns,
        sorted(episodes_rows, key=lambda x: str(x["episode_id"])),
    )
    write_csv(
        steps_all,
        step_columns,
        sorted(steps_rows, key=lambda x: (str(x["episode_id"]), int(x["step_idx"]))),
    )
    write_csv(
        events_all,
        event_columns,
        sorted(events_rows, key=lambda x: (str(x["episode_id"]), int(x["step_idx"]), str(x["event_id"]))),
    )
    return {
        "logs_dir": logs_dir,
        "episodes_all_csv": episodes_all,
        "steps_all_csv": steps_all,
        "events_all_csv": events_all,
    }


def write_condition_cells(
    run_dir: Path,
    episodes_rows: List[Dict[str, Any]],
    steps_rows: List[Dict[str, Any]],
    events_rows: List[Dict[str, Any]],
    episode_columns: List[str],
    step_columns: List[str],
    event_columns: List[str],
) -> Path:
    cells_dir = run_dir / "cells"
    ensure_dir(cells_dir)
    by_episode_condition = {str(ep["episode_id"]): str(ep["condition_id"]) for ep in episodes_rows}
    conditions = sorted({str(ep["condition_id"]) for ep in episodes_rows})

    for condition_id in conditions:
        cell_dir = cells_dir / condition_id
        ensure_dir(cell_dir)
        episodes_cell = [x for x in episodes_rows if str(x["condition_id"]) == condition_id]
        steps_cell = [x for x in steps_rows if by_episode_condition.get(str(x["episode_id"])) == condition_id]
        events_cell = [x for x in events_rows if by_episode_condition.get(str(x["episode_id"])) == condition_id]

        write_csv(
            cell_dir / "episodes.csv",
            episode_columns,
            sorted(episodes_cell, key=lambda x: str(x["episode_id"])),
        )
        write_csv(
            cell_dir / "steps.csv",
            step_columns,
            sorted(steps_cell, key=lambda x: (str(x["episode_id"]), int(x["step_idx"]))),
        )
        write_csv(
            cell_dir / "events.csv",
            event_columns,
            sorted(events_cell, key=lambda x: (str(x["episode_id"]), int(x["step_idx"]), str(x["event_id"]))),
        )
        (cell_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "condition_id": condition_id,
                    "episodes_count": len(episodes_cell),
                    "steps_count": len(steps_cell),
                    "events_count": len(events_cell),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return cells_dir


def write_episode_index(
    run_dir: Path,
    model_id: str,
    episodes_rows: List[Dict[str, Any]],
    steps_rows: List[Dict[str, Any]],
    events_rows: List[Dict[str, Any]],
    frames_rows: List[Dict[str, Any]],
) -> Path:
    steps_count = defaultdict(int)
    events_count = defaultdict(int)
    frames_count = defaultdict(int)
    for row in steps_rows:
        steps_count[str(row["episode_id"])] += 1
    for row in events_rows:
        events_count[str(row["episode_id"])] += 1
    for row in frames_rows:
        frames_count[str(row["episode_id"])] += 1

    rows = []
    for episode in sorted(episodes_rows, key=lambda x: str(x["episode_id"])):
        episode_id = str(episode["episode_id"])
        rows.append(
            {
                "run_id": episode["run_id"],
                "episode_id": episode_id,
                "sequence_id": episode["sequence_id"],
                "condition_id": episode["condition_id"],
                "action_level_id": episode["action_level_id"],
                "scenario_profile_id": episode["scenario_profile_id"],
                "observation_profile_id": episode["observation_profile_id"],
                "status": episode["status"],
                "terminate_reason": episode["terminate_reason"],
                "steps_logged": steps_count[episode_id],
                "events_logged": events_count[episode_id],
                "frames_saved": frames_count[episode_id],
                "cell_dir": str(run_dir / "cells" / str(episode["condition_id"])),
                "frame_dir": str(run_dir / "frames" / model_id / episode_id),
            }
        )

    out = run_dir / "logs" / "episodes_index.csv"
    write_csv(out, EPISODE_INDEX_COLUMNS, rows)
    return out


def write_frames_manifest(run_dir: Path, frames_rows: List[Dict[str, Any]]) -> Path:
    out = run_dir / "frames_manifest.csv"
    ordered = sorted(frames_rows, key=lambda x: (str(x["episode_id"]), int(x["step_idx"])))
    write_csv(out, FRAMES_MANIFEST_COLUMNS, ordered)
    return out

